"""HTTP server exposing termauto's completion endpoint.

The zsh widget POSTs `/complete` with the user's shell state and gets back
a ranked list of candidates. Kept deliberately small — no auth, localhost-only,
single endpoint.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI, Header
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import __version__
from .inference import InferenceEngine
from .prompt import (
    CompletionContext,
    build_inline_messages,
    build_messages,
    is_dangerous,
    parse_candidates,
)

log = logging.getLogger(__name__)


class CompleteRequest(BaseModel):
    cwd: str
    buffer: str = ""
    recent_commands: list[tuple[str, int]] = Field(default_factory=list)
    last_stderr_tail: Optional[str] = None
    dir_listing: Optional[list[str]] = None
    n_candidates: int = 5
    max_tokens: int = 256
    temperature: float = 0.4
    enable_thinking: bool = False


class Candidate(BaseModel):
    cmd: str
    reason: Optional[str] = None


class CompleteResponse(BaseModel):
    candidates: list[Candidate]
    model: str
    elapsed_ms: float


class InlineRequest(BaseModel):
    """Inline (ghost-text) request — single candidate, low latency."""

    cwd: str
    buffer: str = ""
    recent_commands: list[tuple[str, int]] = Field(default_factory=list)
    last_stderr_tail: Optional[str] = None
    max_tokens: int = 40
    temperature: float = 0.2


class InlineResponse(BaseModel):
    """Inline response — full command starting with the buffer, or empty.

    `reason` is informational: "ok" | "empty_buffer" | "no_continuation" |
    "dangerous" | "lock_timeout" | "too_long" | "error".
    """

    suggestion: str
    model: str
    elapsed_ms: float
    reason: Optional[str] = None


def create_app(engine: InferenceEngine) -> FastAPI:
    app = FastAPI(title="termauto", version=__version__)

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "ok": True,
            "model": engine.model_name,
            "loaded": engine.is_loaded,
            "pid": os.getpid(),
            "version": __version__,
        }

    @app.post("/complete", response_model=CompleteResponse)
    def complete(req: CompleteRequest) -> CompleteResponse:
        import time

        ctx = CompletionContext(
            cwd=req.cwd,
            buffer=req.buffer,
            recent_commands=req.recent_commands,
            last_stderr_tail=req.last_stderr_tail,
            dir_listing=req.dir_listing,
            n_candidates=req.n_candidates,
        )
        messages = build_messages(ctx)
        t0 = time.monotonic()
        raw = engine.generate(
            messages,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            enable_thinking=req.enable_thinking,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        parsed = parse_candidates(raw)[: req.n_candidates]
        return CompleteResponse(
            candidates=[Candidate(cmd=c, reason=r) for c, r in parsed],
            model=engine.model_name,
            elapsed_ms=round(elapsed_ms, 1),
        )

    @app.post("/warmup")
    def warmup() -> dict:
        engine.warmup()
        return {"ok": True, "loaded": engine.is_loaded}

    @app.post("/suggest_inline")
    def suggest_inline(req: InlineRequest, accept: str = Header(default="application/json")):
        """Single-candidate ghost-text endpoint.

        Content negotiation:
        - `Accept: text/plain` → returns just the suggestion string (or empty
          body if none). This is what the zsh-autosuggestions strategy uses —
          no JSON parsing dependency in the shell hot path.
        - Anything else → returns InlineResponse JSON with debugging metadata
          (elapsed_ms, reason). Used by `termauto suggest --inline` and tests.
        """
        import time

        t0 = time.monotonic()
        elapsed = lambda: round((time.monotonic() - t0) * 1000.0, 1)
        want_text = accept.startswith("text/plain")

        def _empty(reason: str):
            if want_text:
                return PlainTextResponse("")
            return InlineResponse(
                suggestion="",
                model=engine.model_name,
                elapsed_ms=elapsed(),
                reason=reason,
            )

        def _ok(suggestion: str):
            if want_text:
                return PlainTextResponse(suggestion)
            return InlineResponse(
                suggestion=suggestion,
                model=engine.model_name,
                elapsed_ms=elapsed(),
                reason="ok",
            )

        # Empty / whitespace-only buffer: nothing useful to extend
        if not req.buffer.strip():
            return _empty("empty_buffer")

        ctx = CompletionContext(
            cwd=req.cwd,
            buffer=req.buffer,
            recent_commands=req.recent_commands,
            last_stderr_tail=req.last_stderr_tail,
            n_candidates=1,
        )
        messages = build_inline_messages(ctx)

        chunks: list[str] = []
        try:
            for chunk in engine.generate_stream(
                messages,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                stop_on_newline=True,
            ):
                chunks.append(chunk)
        except Exception as e:  # noqa: BLE001
            log.warning("ghost generation error: %s", e)
            return _empty("error")

        if not chunks:
            # generate_stream returned without yielding — lock contention path
            return _empty("lock_timeout")

        raw = "".join(chunks)
        # First line only (the prompt says "one line" but defend anyway)
        suggestion = raw.split("\n", 1)[0].rstrip()

        # The model must echo the buffer prefix. If it didn't, try the fallback
        # interpretation that it gave us just the suffix — but only when the
        # output looks like a sensible continuation (no leading whitespace).
        if not suggestion.startswith(req.buffer):
            if suggestion and not suggestion[0].isspace():
                suggestion = req.buffer + suggestion
            else:
                return _empty("no_continuation")

        # Length guard — runaway generations or attached comments
        if len(suggestion) > 500:
            return _empty("too_long")

        # Deny-list — single source of truth with the panel parser
        if is_dangerous(suggestion):
            log.warning("ghost: dropped dangerous suggestion: %.60s", suggestion)
            return _empty("dangerous")

        # No-op: model echoed the buffer with nothing added
        if suggestion == req.buffer:
            return _empty("no_continuation")

        return _ok(suggestion)

    return app
