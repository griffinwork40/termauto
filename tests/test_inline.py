"""Coverage for inline (ghost-text) prompt + endpoint plumbing.

The model itself isn't loaded here — InferenceEngine is monkeypatched to
yield canned chunks. We exercise the prompt builder, the FastAPI endpoint's
post-processing pipeline (prefix validation, deny-list, content negotiation,
no-op cases), and the public `is_dangerous` alias.
"""

from __future__ import annotations

from typing import Iterable

import pytest
from fastapi.testclient import TestClient

from termauto.inference import InferenceEngine
from termauto.prompt import (
    INLINE_SYSTEM_PROMPT,
    CompletionContext,
    build_inline_messages,
    is_dangerous,
)
from termauto.server import create_app


# ---------------------------- prompt construction ---------------------------

def test_inline_messages_have_expected_shape():
    ctx = CompletionContext(cwd="/tmp", buffer="git ", recent_commands=[("ls", 0)])
    msgs = build_inline_messages(ctx)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[0]["content"] == INLINE_SYSTEM_PROMPT
    assert "buffer: git " in msgs[-1]["content"]
    assert "cwd: /tmp" in msgs[-1]["content"]


def test_inline_messages_distinct_system_from_panel():
    """Inline must NOT reuse the panel's 'numbered list' prompt — that would
    blow the contract that ghost text is a single command starting with the
    buffer."""
    from termauto.prompt import SYSTEM_PROMPT

    assert INLINE_SYSTEM_PROMPT != SYSTEM_PROMPT
    assert "numbered list" not in INLINE_SYSTEM_PROMPT.lower()
    assert "one line" in INLINE_SYSTEM_PROMPT.lower() or "single" in INLINE_SYSTEM_PROMPT.lower()


# ---------------------------- public predicate ------------------------------

def test_is_dangerous_public_alias_blocks_known_destructive():
    assert is_dangerous("rm -rf /")
    assert is_dangerous("sudo rm -rf /etc")
    assert is_dangerous("git push --force origin main")
    assert is_dangerous("git reset --hard HEAD~3")
    assert is_dangerous("mkfs.ext4 /dev/sda1")


def test_is_dangerous_public_alias_permits_benign():
    assert not is_dangerous("rm foo.txt")
    assert not is_dangerous("git push --force-with-lease")
    assert not is_dangerous("git checkout -b feature/x")
    assert not is_dangerous("ls -la")


# ---------------------------- endpoint plumbing -----------------------------

class _FakeEngine:
    """Stand-in for InferenceEngine. We feed canned chunk sequences and
    record what messages we were called with, without loading any model."""

    def __init__(self, chunks: Iterable[str], *, name: str = "fake/model"):
        self.chunks = list(chunks)
        self.model_name = name
        self.is_loaded = True
        self.last_messages: list[dict] | None = None
        self.last_kwargs: dict | None = None

    def generate_stream(self, messages, **kwargs):
        self.last_messages = messages
        self.last_kwargs = kwargs
        for c in self.chunks:
            yield c

    def warmup(self):  # endpoint doesn't call this, but the app needs the attr
        pass


def _client(chunks: Iterable[str]) -> tuple[TestClient, _FakeEngine]:
    engine = _FakeEngine(chunks)
    app = create_app(engine)  # type: ignore[arg-type]
    return TestClient(app), engine


def test_inline_endpoint_returns_full_command_with_buffer_prefix():
    client, _ = _client(["git checkout -b feature/", "user-auth\n# extra"])
    r = client.post("/suggest_inline", json={"cwd": "/tmp", "buffer": "git checkout -b feature/"})
    assert r.status_code == 200
    body = r.json()
    assert body["suggestion"] == "git checkout -b feature/user-auth"
    assert body["reason"] == "ok"


def test_inline_endpoint_handles_suffix_only_output():
    """Model didn't include the buffer prefix — server prepends it if the
    suffix looks like a sensible continuation (no leading whitespace)."""
    client, _ = _client(["main\n"])
    r = client.post("/suggest_inline", json={"cwd": "/tmp", "buffer": "git checkout "})
    assert r.json()["suggestion"] == "git checkout main"


def test_inline_endpoint_rejects_dangerous_output():
    client, _ = _client(["rm -rf /\n"])
    r = client.post("/suggest_inline", json={"cwd": "/tmp", "buffer": "rm -rf "})
    body = r.json()
    assert body["suggestion"] == ""
    assert body["reason"] == "dangerous"


def test_inline_endpoint_empty_buffer_short_circuit():
    client, engine = _client(["should not be called"])
    r = client.post("/suggest_inline", json={"cwd": "/tmp", "buffer": "   "})
    assert r.json()["suggestion"] == ""
    assert r.json()["reason"] == "empty_buffer"
    # And the engine was NOT called at all — no wasted inference
    assert engine.last_messages is None


def test_inline_endpoint_noop_when_model_just_echoes_buffer():
    client, _ = _client(["git status"])  # exactly the buffer
    r = client.post("/suggest_inline", json={"cwd": "/tmp", "buffer": "git status"})
    assert r.json()["suggestion"] == ""
    assert r.json()["reason"] == "no_continuation"


def test_inline_endpoint_text_plain_returns_bare_string():
    client, _ = _client(["git status -sb\n"])
    r = client.post(
        "/suggest_inline",
        json={"cwd": "/tmp", "buffer": "git status"},
        headers={"Accept": "text/plain"},
    )
    assert r.status_code == 200
    assert r.text == "git status -sb"
    assert "application/json" not in r.headers.get("content-type", "")


def test_inline_endpoint_text_plain_empty_body_on_no_suggestion():
    client, _ = _client(["rm -rf /\n"])
    r = client.post(
        "/suggest_inline",
        json={"cwd": "/tmp", "buffer": "rm -rf "},
        headers={"Accept": "text/plain"},
    )
    assert r.status_code == 200
    assert r.text == ""


def test_inline_endpoint_lock_timeout_yields_empty():
    """Engine returns immediately without yielding anything — simulates
    the lock-contention degradation path."""
    client, _ = _client([])  # no chunks at all
    r = client.post("/suggest_inline", json={"cwd": "/tmp", "buffer": "ls "})
    assert r.json()["suggestion"] == ""
    assert r.json()["reason"] == "lock_timeout"


def test_inline_endpoint_truncates_at_first_newline():
    """Streaming must stop at the first newline; trailing-line garbage from
    a misbehaving model must not leak into the suggestion."""
    client, _ = _client(["ls -la\n", "rm -rf /\n"])
    r = client.post("/suggest_inline", json={"cwd": "/tmp", "buffer": "ls "})
    assert r.json()["suggestion"] == "ls -la"


def test_inline_endpoint_passes_max_tokens_and_temperature():
    client, engine = _client(["echo hi\n"])
    client.post(
        "/suggest_inline",
        json={"cwd": "/tmp", "buffer": "echo ", "max_tokens": 24, "temperature": 0.1},
    )
    assert engine.last_kwargs["max_tokens"] == 24
    assert engine.last_kwargs["temperature"] == 0.1
