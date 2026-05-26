"""Regression coverage for prompt parsing + dangerous-command filtering.

Run: .venv/bin/python -m pytest tests/  (no pytest config needed)
"""

from termauto.prompt import _is_dangerous, parse_candidates


# ----------------------------- dangerous filter -----------------------------

def test_dangerous_blocks_destructive_defaults():
    must_block = [
        "rm -rf /",
        "rm -rf / ",
        "rm -rf ~",
        "rm -rf ~/",
        "rm -rf *",
        "sudo rm -rf /var/cache",
        "git push --force",
        "git push --force origin main",
        "git push -f origin main",
        "git reset --hard HEAD~1",
        "dd if=/dev/zero of=/dev/disk0",
        "chmod -R 777 /",
        ":(){ :|:& };:",
        "mkfs.ext4 /dev/sda1",
    ]
    for cmd in must_block:
        assert _is_dangerous(cmd), f"should have blocked: {cmd!r}"


def test_dangerous_allows_safe_lookalikes():
    must_allow = [
        "rm -rf /tmp/build",          # tmp subdir
        "rm -rf ~/Downloads/old",     # home subdir
        "rm -rf ~/.cache",            # home subdir
        "git push --force-with-lease",  # safe variant
        "git push --force-with-lease origin main",
        "git reset --soft HEAD~1",    # soft, not hard
        "git reset HEAD file.py",     # default = mixed, not hard
        "dd if=foo of=bar.img",       # not raw block device
        "chmod -R 755 ./build",       # not 777, not /
        "ls -la",
        "echo hello",
    ]
    for cmd in must_allow:
        assert not _is_dangerous(cmd), f"should have allowed: {cmd!r}"


# ----------------------------- parser ---------------------------------------

def test_parse_requires_list_marker():
    # No list markers anywhere -> empty result
    raw = "Sure! Here's what I think.\nls -la\npwd\nHope that helps!"
    assert parse_candidates(raw) == []


def test_parse_strips_preamble_and_postamble():
    raw = """Sure! Here are some options:
1. `ls -la`
2. `pwd`
Hope this helps!"""
    out = parse_candidates(raw)
    assert [c for c, _ in out] == ["ls -la", "pwd"]


def test_parse_handles_styles():
    for raw in [
        "1. ls\n2. pwd",
        "1) ls\n2) pwd",
        "- ls\n- pwd",
        "* ls\n* pwd",
        "• ls\n• pwd",
    ]:
        cmds = [c for c, _ in parse_candidates(raw)]
        assert cmds == ["ls", "pwd"], f"failed on: {raw!r}"


def test_parse_extracts_reason():
    out = parse_candidates("1. git status  # check working tree")
    assert out == [("git status", "check working tree")]

    out = parse_candidates("1. git status # check working tree")  # single space
    assert out == [("git status", "check working tree")]


def test_parse_dedups():
    raw = "1. ls\n2. ls\n3. pwd"
    cmds = [c for c, _ in parse_candidates(raw)]
    assert cmds == ["ls", "pwd"]


def test_parse_drops_dangerous():
    raw = """1. echo hello
2. rm -rf /
3. git push --force
4. echo world"""
    cmds = [c for c, _ in parse_candidates(raw)]
    assert cmds == ["echo hello", "echo world"]


def test_parse_caps_at_8():
    raw = "\n".join(f"{i}. cmd{i}" for i in range(1, 20))
    cmds = [c for c, _ in parse_candidates(raw)]
    assert len(cmds) == 8


def test_parse_unwraps_single_backticks():
    raw = "1. `ls -la`"
    out = parse_candidates(raw)
    assert out == [("ls -la", None)]


def test_parse_drops_code_fences():
    raw = "```bash\n1. ls\n```"
    cmds = [c for c, _ in parse_candidates(raw)]
    # The numbered line inside should still parse, the fence lines should not
    assert "ls" in cmds


if __name__ == "__main__":
    # Run directly without pytest
    import sys
    import traceback

    failed = 0
    passed = 0
    for name, fn in list(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  ✓ {name}")
            passed += 1
        except AssertionError as e:  # noqa: PERF203
            print(f"  ✗ {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    sys.exit(0 if failed == 0 else 1)
