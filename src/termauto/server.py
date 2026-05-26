"""HTTP server exposing termauto's completion endpoint.

The zsh widget POSTs `/complete` with the user's shell state and gets back
a ranked list of candidates. Kept deliberately small — no auth, localhost-only,
single endpoint.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from . import __version__
from .inference import InferenceEngine
from .prompt import CompletionContext, build_messages, parse_candidates

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


class Candidate(BaseModel):
    cmd: str
    reason: Optional[str] = None


class CompleteResponse(BaseModel):
    candidates: list[Candidate]
    model: str
    elapsed_ms: float


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

    return app
