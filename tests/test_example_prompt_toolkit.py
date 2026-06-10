"""Coverage for examples/prompt_toolkit/termauto_suggest.py.

The example file is the contract for the first non-zsh integration of
termauto. These tests assert the load-bearing invariants:

- Prefix-match guard fires when daemon returns a non-extending response.
- Timeout/connection failures never raise into the caller.
- The "warn once on refused connection" contract holds across multiple calls.
- The async path actually runs in a worker thread, not on the event loop.
- Cheap guards (short buffer, leading whitespace) short-circuit before
  touching the network.
- Daemon's empty body / no-continuation maps to None.

Tests don't require the termauto daemon to be running — `urllib.request.urlopen`
is patched out at the boundary.
"""

from __future__ import annotations

import asyncio
import io
import sys
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from prompt_toolkit.document import Document

# Add examples/prompt_toolkit to the path so we can import the file under test
EXAMPLE_DIR = Path(__file__).parent.parent / "examples" / "prompt_toolkit"
sys.path.insert(0, str(EXAMPLE_DIR))

from termauto_suggest import TermautoAutoSuggest  # noqa: E402


# ----------------------------- test scaffolding -----------------------------


class _FakeResponse:
    """Minimal urllib response stand-in supporting the context-manager + .read() shape."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


@contextmanager
def _mock_urlopen(*, body: bytes = b"", raises: Exception | None = None):
    """Patch the urlopen call inside the example module specifically."""
    def fake(req, timeout=None):
        if raises is not None:
            raise raises
        return _FakeResponse(body)

    with patch("termauto_suggest.urllib.request.urlopen", side_effect=fake) as m:
        yield m


def _doc(text: str) -> Document:
    return Document(text=text, cursor_position=len(text))


def _suggest(client: TermautoAutoSuggest, text: str):
    """Drive the sync path directly — same code path the async path delegates to."""
    return client.get_suggestion(buffer=None, document=_doc(text))


# ----------------------------- happy path -----------------------------------


def test_happy_path_returns_suffix_only():
    """prompt_toolkit's Suggestion takes the suffix (text after cursor),
    not the full command. The example must strip the buffer prefix."""
    client = TermautoAutoSuggest()
    with _mock_urlopen(body=b"git status"):
        result = _suggest(client, "git ")
    assert result is not None
    assert result.text == "status"


def test_happy_path_longer_buffer():
    client = TermautoAutoSuggest()
    with _mock_urlopen(body=b"git checkout -b feature/user-auth"):
        result = _suggest(client, "git checkout -b feature/")
    assert result.text == "user-auth"


# ----------------------------- prefix-match guard ---------------------------


def test_prefix_mismatch_drops_suggestion():
    """The load-bearing safety invariant: if the daemon returns a response
    that does NOT extend the buffer, the example must return None. Returning
    Suggestion(non-prefix) would let prompt_toolkit replace user input on accept."""
    client = TermautoAutoSuggest()
    # Daemon returned something entirely unrelated to "git "
    with _mock_urlopen(body=b"ls -la"):
        result = _suggest(client, "git ")
    assert result is None


def test_prefix_match_is_byte_exact_not_fuzzy():
    """Whitespace and case differences count as mismatches. We do not 'help'
    by normalizing — the user's literal input is the contract."""
    client = TermautoAutoSuggest()
    # Buffer has trailing space; response doesn't
    with _mock_urlopen(body=b"git status"):
        result = _suggest(client, "Git ")
    assert result is None


# ----------------------------- failure modes --------------------------------


def test_empty_body_returns_none():
    """Daemon's deny-list / no-continuation paths return empty text/plain body."""
    client = TermautoAutoSuggest()
    with _mock_urlopen(body=b""):
        result = _suggest(client, "rm -rf ")
    assert result is None


def test_no_op_response_equal_to_buffer_returns_none():
    """If the daemon echoes the buffer with no continuation, suffix is empty
    → no Suggestion should be emitted (would render as empty ghost text)."""
    client = TermautoAutoSuggest()
    with _mock_urlopen(body=b"git status"):
        result = _suggest(client, "git status")
    assert result is None


def test_timeout_returns_none_silently():
    """Slow daemons must never freeze the UI or raise. Silent None always."""
    client = TermautoAutoSuggest()
    with _mock_urlopen(raises=TimeoutError("timed out")):
        result = _suggest(client, "docker run ")
    assert result is None


def test_oserror_returns_none_silently():
    """OSError covers misc network glitches (broken pipe, etc.)."""
    client = TermautoAutoSuggest()
    with _mock_urlopen(raises=OSError("broken pipe")):
        result = _suggest(client, "python ")
    assert result is None


# ----------------------------- warn-once contract ---------------------------


def test_refused_connection_warns_once_to_stderr(capsys):
    """First refused connection writes ONE stderr line. Subsequent refused
    connections are silent. This is the spam-once contract — silent failure
    hides daemon-down from users; per-keystroke warnings are unbearable."""
    client = TermautoAutoSuggest()
    refused = urllib.error.URLError(reason=ConnectionRefusedError("refused"))

    with _mock_urlopen(raises=refused):
        result1 = _suggest(client, "git ")
        result2 = _suggest(client, "git s")
        result3 = _suggest(client, "git st")

    assert result1 is None
    assert result2 is None
    assert result3 is None

    captured = capsys.readouterr()
    # Exactly one stderr line, mentions the daemon URL and the start command
    assert captured.err.count("\n") == 1
    assert "daemon not reachable" in captured.err
    assert "termauto start" in captured.err
    assert client.url in captured.err


def test_other_urlerror_does_not_warn(capsys):
    """Non-refused URLErrors (DNS, malformed URL, etc.) should NOT trip the
    daemon-down warning — that warning is specifically about a missing local
    daemon."""
    client = TermautoAutoSuggest()
    other = urllib.error.URLError(reason="some other reason")
    with _mock_urlopen(raises=other):
        _suggest(client, "git ")
    captured = capsys.readouterr()
    assert captured.err == ""


def test_warn_state_isolated_per_instance(capsys):
    """Each TermautoAutoSuggest instance has its own warn-once budget.
    (A REPL that creates a new session shouldn't be silenced by an earlier one.)"""
    refused = urllib.error.URLError(reason=ConnectionRefusedError("refused"))
    with _mock_urlopen(raises=refused):
        client_a = TermautoAutoSuggest()
        client_b = TermautoAutoSuggest()
        _suggest(client_a, "git ")
        _suggest(client_b, "git ")
    captured = capsys.readouterr()
    assert captured.err.count("\n") == 2


# ----------------------------- cheap guards ---------------------------------


def test_short_buffer_does_not_call_daemon():
    """Buffers below min_chars should skip the HTTP round-trip entirely."""
    client = TermautoAutoSuggest(min_chars=3)
    with _mock_urlopen(body=b"git status") as m:
        result = _suggest(client, "gi")  # 2 chars, min is 3
    assert result is None
    assert m.call_count == 0


def test_leading_whitespace_buffer_does_not_call_daemon():
    """Buffers starting with whitespace are usually mid-edit indentation.
    Skip the daemon call rather than waste a generation."""
    client = TermautoAutoSuggest()
    with _mock_urlopen(body=b"  git status") as m:
        result = _suggest(client, " git ")
    assert result is None
    assert m.call_count == 0


# ----------------------------- async-thread offload -------------------------


def test_async_path_runs_in_worker_thread():
    """The async path MUST NOT call get_suggestion on the event-loop thread —
    a blocking 500ms urllib call would freeze the prompt_toolkit UI. This
    test verifies the offload is real, not just a comment in the code."""
    import threading

    client = TermautoAutoSuggest()
    main_thread_id = threading.get_ident()
    call_thread_ids: list[int] = []

    real_suggest = client._suggest

    def tracking_suggest(text):
        call_thread_ids.append(threading.get_ident())
        return real_suggest(text)

    client._suggest = tracking_suggest

    with _mock_urlopen(body=b"git status"):
        result = asyncio.run(client.get_suggestion_async(buff=None, document=_doc("git ")))

    assert result is not None
    assert result.text == "status"
    assert len(call_thread_ids) == 1
    assert call_thread_ids[0] != main_thread_id, (
        "get_suggestion_async ran _suggest on the event-loop thread — "
        "this will freeze the prompt_toolkit UI during daemon calls"
    )


# ----------------------------- request payload ------------------------------


def test_request_uses_text_plain_accept_header():
    """We send Accept: text/plain so the daemon's content negotiation returns
    the bare suggestion rather than JSON we'd have to parse."""
    client = TermautoAutoSuggest()
    with _mock_urlopen(body=b"git status") as m:
        _suggest(client, "git ")
    req = m.call_args.args[0]
    # urllib.request normalizes header names — capitalize-first
    headers = {k.lower(): v for k, v in req.header_items()}
    assert headers.get("accept") == "text/plain"
    assert headers.get("content-type") == "application/json"


def test_request_payload_contains_buffer_and_cwd():
    """Daemon needs both buffer and cwd to ground completions."""
    import json
    client = TermautoAutoSuggest()
    with _mock_urlopen(body=b"git status") as m:
        _suggest(client, "git ")
    req = m.call_args.args[0]
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["buffer"] == "git "
    assert "cwd" in payload
    assert isinstance(payload.get("recent_commands"), list)


def test_custom_url_and_timeout_passthrough():
    """Constructor knobs end up at the network boundary."""
    client = TermautoAutoSuggest(url="http://localhost:9999/x", timeout_s=0.05)
    with _mock_urlopen(body=b"") as m:
        _suggest(client, "git ")
    assert m.call_count == 1
    # Second positional arg of urlopen (after the Request) is timeout
    kwargs = m.call_args.kwargs
    assert kwargs.get("timeout") == 0.05
    req = m.call_args.args[0]
    assert req.full_url == "http://localhost:9999/x"
