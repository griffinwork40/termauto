"""Prompt construction for terminal completion.

The model is asked to produce N candidate shell commands given:
- current working directory
- recent commands (with exit codes)
- the current buffer (what the user has typed so far)
- optional last stderr tail
- optional directory listing snapshot

Output format is strict: one candidate per line, optionally followed by
"  # short reason". The daemon parses lines starting with a digit + ". ".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

SYSTEM_PROMPT = """You are termauto, a local shell-command completion assistant.

You suggest 3-5 plausible next commands the user might run, given their current
shell state. Output ONLY a numbered list. Each line is a single shell command,
optionally followed by `  # short reason` (max 8 words).

Rules:
- Output nothing except the numbered list. No preamble, no markdown, no code fences.
- Each candidate must be a single line, executable as-is.
- Prefer commands that complete or extend the user's current buffer.
- Avoid destructive defaults: never suggest `rm -rf`, `git push --force`, `git reset --hard`, `sudo rm`, `dd of=`, `> file` overwriting existing files, or anything that drops data, unless the user's buffer clearly already moves in that direction.
- If the buffer is empty, suggest commands sensible for the cwd and recent history.
- If the last command failed (exit != 0), the top suggestion should plausibly recover or fix it.
- Use absolute or relative paths that actually exist in the cwd when possible.
"""

EXAMPLE_USER = """cwd: /Users/alice/projects/api
recent commands:
  $ git status            (exit 0)
  $ pytest tests/         (exit 1)
last stderr tail:
  FAILED tests/test_auth.py::test_login - AssertionError
buffer: pytest tests/test_auth.py"""

EXAMPLE_ASSISTANT = """1. pytest tests/test_auth.py::test_login -x  # rerun failing test
2. pytest tests/test_auth.py -v  # verbose to see why
3. pytest tests/test_auth.py --pdb  # drop into debugger on fail
4. pytest tests/test_auth.py -k login  # filter by name"""


@dataclass
class CompletionContext:
    """Everything the model sees about the user's shell state."""

    cwd: str
    buffer: str = ""
    recent_commands: list[tuple[str, int]] = field(default_factory=list)
    last_stderr_tail: Optional[str] = None
    dir_listing: Optional[list[str]] = None
    n_candidates: int = 5

    def render_user_message(self) -> str:
        parts: list[str] = []
        parts.append(f"cwd: {self.cwd}")

        if self.recent_commands:
            parts.append("recent commands:")
            for cmd, exit_code in self.recent_commands[-8:]:
                cmd_truncated = cmd if len(cmd) <= 120 else cmd[:117] + "..."
                parts.append(f"  $ {cmd_truncated}  (exit {exit_code})")

        if self.last_stderr_tail:
            tail = self.last_stderr_tail.strip()
            if len(tail) > 400:
                tail = tail[:397] + "..."
            parts.append("last stderr tail:")
            for line in tail.splitlines():
                parts.append(f"  {line}")

        if self.dir_listing:
            entries = self.dir_listing[:20]
            parts.append(f"dir listing ({len(self.dir_listing)} entries, showing {len(entries)}):")
            parts.append("  " + "  ".join(entries))

        parts.append(f"buffer: {self.buffer}")
        return "\n".join(parts)


def build_messages(ctx: CompletionContext) -> list[dict[str, str]]:
    """Build the chat-completion message list for mlx-lm.

    One-shot in-context example plus the live user message.
    Small models follow strict formats much better with an example.
    """
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": EXAMPLE_USER},
        {"role": "assistant", "content": EXAMPLE_ASSISTANT},
        {"role": "user", "content": ctx.render_user_message()},
    ]


# Defense in depth: even though the system prompt forbids these, parse-time
# filtering catches model mistakes. Patterns are intentionally narrow — we
# only block clearly destructive defaults, not anything containing the word.
DANGEROUS_PATTERNS = [
    r"\brm\s+-[rRf]+\s+/(?:\s|$)",            # rm -rf /     (root)
    r"\brm\s+-[rRf]+\s+~/?(?:\s|$)",          # rm -rf ~ or ~/  (bare home; sub-paths ok)
    r"\brm\s+-[rRf]+\s+\*\s*$",                # rm -rf *
    r"\bsudo\s+rm\s+-[rRf]+\s+",               # sudo rm -rf anything
    r"\bgit\s+push\b[^\n]*\s--force(?!-)\b",   # --force but NOT --force-with-lease
    r"\bgit\s+push\b[^\n]*\s-f(?:\s|$)",       # -f short form
    r"\bgit\s+reset\s+--hard\b",               # git reset --hard
    r"\bdd\s+[^\n]*\bof=/dev/(?:disk|sd|nvme)", # dd of=/dev/disk0 et al
    r":\(\)\s*\{[^}]*\|[^}]*\&[^}]*\};\s*:",   # classic fork bomb
    r"\bmkfs\.",                                # mkfs.ext4 etc
    r"\bchmod\s+-R\s+0?777\s+/",                # recursive 777 on /
    r">\s*/dev/sd[a-z]",                       # redirect to raw block device
]
_DANGEROUS_RE: list = []  # lazily compiled


def _is_dangerous(cmd: str) -> bool:
    import re

    global _DANGEROUS_RE
    if not _DANGEROUS_RE:
        _DANGEROUS_RE = [re.compile(p) for p in DANGEROUS_PATTERNS]
    return any(rx.search(cmd) for rx in _DANGEROUS_RE)


def parse_candidates(raw_output: str) -> list[tuple[str, Optional[str]]]:
    """Parse model output into [(command, reason), ...].

    Strict: only accepts lines that began with a list marker (`1.`, `1)`,
    `-`, `*`, `•`). Anything else — preambles, postambles, prose — is
    dropped. This keeps small-model misbehavior from polluting the panel.

    Also filters dangerous-default commands (`rm -rf /`, `git push --force`,
    etc.) as a second line of defense beyond the system prompt.
    """
    import re

    list_marker_re = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.*)$")
    candidates: list[tuple[str, Optional[str]]] = []
    seen: set[str] = set()

    for raw in raw_output.splitlines():
        m = list_marker_re.match(raw)
        if not m:
            continue  # require list marker — drops preambles/postambles
        line = m.group(1).strip()
        if not line:
            continue

        # Split off "  # reason" suffix
        cmd, _, reason = line.partition("  #")
        if not reason:
            cmd, _, reason = line.partition(" #")
        cmd = cmd.strip()
        reason = reason.strip() or None

        # Strip surrounding backticks if the model wrapped it
        if cmd.startswith("`") and cmd.endswith("`") and len(cmd) > 1:
            cmd = cmd[1:-1].strip()

        # Strip surrounding triple-backtick code fences (rare but possible)
        if cmd.startswith("```"):
            continue

        if not cmd or cmd.startswith("#") or len(cmd) > 500:
            continue
        if cmd in seen:
            continue
        if _is_dangerous(cmd):
            continue

        seen.add(cmd)
        candidates.append((cmd, reason))

        if len(candidates) >= 8:
            break

    return candidates
