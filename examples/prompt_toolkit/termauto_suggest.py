"""termauto ghost text inside any prompt_toolkit-based CLI.

Drop-in example: copy this single file into your project and wire it into
your prompt_toolkit Session to get local-MLX ghost text completion. Works
inside ipython, ptpython, click-repl, pgcli, mycli, glances, sqlmap, and
any other prompt_toolkit-based REPL or TUI.

USAGE
-----
1. Install + start the termauto daemon:
       pip install termauto
       termauto start                 # leave running in the background

2. Copy this file into your project (or import as a module).

3. Wire it into your prompt_toolkit Session:

       from prompt_toolkit import PromptSession
       from termauto_suggest import TermautoAutoSuggest

       session = PromptSession(auto_suggest=TermautoAutoSuggest())
       while True:
           line = session.prompt("> ")
           # ... your REPL logic

4. Start typing. Greyed ghost text appears past the cursor.
   Right-arrow / End / Ctrl-F accepts. Just keep typing to ignore.

REQUIREMENTS
------------
- prompt_toolkit (already in your stack if you're using ipython/ptpython/etc.)
- Python 3.10+ for asyncio.to_thread
- No other dependencies — uses stdlib urllib so this example runs on a fresh
  prompt_toolkit project without any new pip installs.

WHY THIS EXISTS
---------------
This is the *first* non-zsh integration of termauto. The patterns here are
not yet stable API — they're a tracer round. If you adapt this to another
TUI framework (bubbletea, ink, ratatui, textual) please open an issue on the
termauto repo with what you needed; that's how the SDK shape gets carved out.

LICENSE: same as termauto.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Optional

from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document


class TermautoAutoSuggest(AutoSuggest):
    """prompt_toolkit AutoSuggest backed by the termauto daemon.

    Failure modes (all return None — no exception escapes into your REPL):

    - **Daemon not running:** prints ONE warning to stderr the first time
      we see a refused connection, then stays silent for the rest of the
      session. (A spam-once contract — silent failure hides config problems
      from users, but per-keystroke warnings would be unbearable.)

    - **Timeout (default 500ms):** silent None. Slow daemons (cold start,
      heavy model, lock contention) should never freeze the UI.

    - **Response doesn't extend the buffer prefix:** silent None. This is
      the **load-bearing safety invariant** — if the model hallucinates a
      *replacement* for your input rather than a *continuation*, we drop
      it entirely. Without this guard, ghost text could rewrite what you
      typed when you accept it.

    - **Empty / dangerous / no-continuation:** the daemon itself returns
      an empty body in these cases (see termauto's server-side deny-list);
      we map that to None.
    """

    DEFAULT_URL = "http://127.0.0.1:8765/suggest_inline"
    DEFAULT_TIMEOUT_S = 0.5
    DEFAULT_MIN_CHARS = 2

    def __init__(
        self,
        url: str = DEFAULT_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        min_chars: int = DEFAULT_MIN_CHARS,
    ):
        self.url = url
        self.timeout_s = timeout_s
        self.min_chars = min_chars
        # Tracks whether we've already complained about a refused connection
        # this session. Reset by reinstantiating.
        self._warned_connection_error = False

    # -- prompt_toolkit contract --------------------------------------------

    def get_suggestion(
        self, buffer: Buffer, document: Document
    ) -> Optional[Suggestion]:
        """Synchronous path. Prefer the async path; this exists because
        prompt_toolkit's abstract base requires it."""
        return self._suggest(document.text_before_cursor)

    async def get_suggestion_async(
        self, buff: Buffer, document: Document
    ) -> Optional[Suggestion]:
        """Async path — runs the blocking HTTP call in a thread so the
        prompt_toolkit event loop is never frozen by daemon latency.

        Without this override, prompt_toolkit's default impl would call
        the sync version inline, blocking the UI for up to `timeout_s`.
        """
        return await asyncio.to_thread(self._suggest, document.text_before_cursor)

    # -- internals ----------------------------------------------------------

    def _suggest(self, text: str) -> Optional[Suggestion]:
        # Cheap guards before we burn a daemon round-trip.
        if len(text) < self.min_chars:
            return None
        if text.startswith(" "):
            return None

        payload = json.dumps(
            {
                "buffer": text,
                "cwd": os.getcwd(),
                "recent_commands": [],  # populate from your REPL if available
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "text/plain",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as e:
            # Distinguish daemon-not-running from transient network errors.
            # `URLError.reason` is the underlying exception or string.
            reason = getattr(e, "reason", None)
            if isinstance(reason, ConnectionRefusedError) and not self._warned_connection_error:
                self._warned_connection_error = True
                print(
                    f"termauto: daemon not reachable at {self.url} — "
                    "ghost text disabled this session. "
                    "Start it with `termauto start`.",
                    file=sys.stderr,
                )
            return None
        except (TimeoutError, OSError):
            return None

        if not body:
            return None

        # PREFIX-MATCH GUARD — explicit, enforced in code, not left to intuition.
        # If the daemon's response doesn't begin with the user's literal buffer,
        # we drop it. Returning Suggestion(non-prefix) would let prompt_toolkit
        # replace the user's input when they hit Right-arrow / End to accept.
        if not body.startswith(text):
            return None

        suffix = body[len(text):]
        if not suffix:
            return None

        return Suggestion(suffix)
