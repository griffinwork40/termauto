# termauto

**Local-first LLM shell completion. Cotabby for the terminal.**

`termauto` watches your zsh prompt. Press `Ctrl-Space` (or type `??` at line
start) and it generates 3–5 ranked candidate commands using a small local
LLM running on Apple Silicon via [MLX](https://github.com/ml-explore/mlx).
Pick one with a number key, or `e` to drop it into the buffer for editing.

Nothing leaves your Mac. No cloud round-trip.

---

## Why this shape (and not ghost text)

The cotabby pattern is ghost text + Tab. That works great for prose. It is a
**footgun in a shell**: a hallucinated `rm -rf` or `git push --force` that you
reflex-Tab-Enter before reading does real damage.

termauto separates *generation* from *commitment*:

- **Generation** is fast and speculative. The daemon returns 3–5 candidates.
- **Commitment** is deliberate. You read, then press a number to accept.
- `$BUFFER` is never mutated until you pick.

Once you trust the model against *your* history and cwd corpus, a future
`--ghost` mode can be added as an additive rendering path. The daemon, prompt
schema, and ranker built here carry over unchanged.

---

## Requirements

- macOS, Apple Silicon (M-series)
- Python 3.10+
- zsh 5.1+
- ~1GB free disk for the default model

---

## Install

```bash
git clone <this-repo> termauto
cd termauto
./install.sh
```

The installer:
1. Creates `.venv/` in the repo
2. Installs the `termauto` package (editable)
3. Writes a `~/.local/bin/termauto` shim onto `$PATH`
4. Prefetches the default model (`mlx-community/Qwen3-1.7B-4bit`, ~1GB)
5. Appends `source <repo>/shell/termauto.zsh` to your `~/.zshrc`

---

## Use

```bash
termauto start         # boot the daemon (loads model warm)
termauto status        # check it's alive
# in any zsh, hit Ctrl-Space at the prompt
termauto stop          # shut it down
```

CLI sanity-check (bypasses the shell widget):

```bash
termauto suggest "git "                     # show ranked candidates
termauto suggest --format json "docker "    # full response with timings
termauto suggest --format lines "ls "       # one cmd per line — same format the widget consumes
```

---

## Config

All of these are env vars set **before** sourcing `termauto.zsh`:

| Variable | Default | Meaning |
|---|---|---|
| `TERMAUTO_BIN` | `termauto` | CLI binary |
| `TERMAUTO_HOTKEY` | `^@` (Ctrl-Space) | zsh keybinding to invoke panel |
| `TERMAUTO_QQ_TRIGGER` | `0` | Set to `1` to fire on `??` at line start |
| `TERMAUTO_N_CANDIDATES` | `5` | Candidates per request |
| `TERMAUTO_HISTORY_DEPTH` | `8` | Recent commands to send as context |
| `TERMAUTO_TIMEOUT` | `15` | CLI request timeout in seconds |

Daemon flags:

```bash
termauto start --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit  # code-tuned alt
termauto start --model mlx-community/Qwen3-0.6B-4bit                    # smaller/faster
termauto start --port 8765 --host 127.0.0.1
termauto start --foreground          # don't daemonize; useful for debugging
termauto start --no-warmup           # skip warmup generation
```

### Model choice

Default is `mlx-community/Qwen3-1.7B-4bit` (~1GB, ~600ms/completion). Qwen3 is
a reasoning model; termauto disables `<think>` mode by default (set
`--thinking` on `suggest` to re-enable; not recommended for shell use — adds
~1.4s/request with no quality gain).

Tested alternatives:

| Model | Size | Latency | Notes |
|---|---|---|---|
| `Qwen3-1.7B-4bit` (default) | ~1GB | ~600ms | Newer generation, good diversity |
| `Qwen2.5-Coder-1.5B-Instruct-4bit` | ~900MB | ~580ms | Code-tuned, reliable 3-5 candidates |
| `Qwen3-0.6B-4bit` | ~400MB | ~300ms | Faster but quality drops noticeably |

Qwen3.6 (the latest Qwen series) has no small variants — the floor is 27B
dense or 35B-A3B MoE, both too large for keystroke latency. If you have
enough RAM (~20GB free), `Qwen3-30B-A3B-4bit` is the highest-quality option
that's still feasible (3B active params keeps latency reasonable).

---

## How it talks to itself

```
┌─────────────────────────────────────────────────────────────┐
│                          your zsh                            │
│   ┌──────────────────────┐                                   │
│   │ termauto.zsh widget  │  ← Ctrl-Space binding             │
│   │   ↓ POSIX exec       │                                   │
│   │   termauto suggest …  │                                   │
│   └──────────────────────┘                                   │
└──────────────│──────────────────────────────────────────────┘
               │ HTTP localhost:8765
               ▼
┌─────────────────────────────────────────────────────────────┐
│                      termauto daemon                         │
│  FastAPI /complete  →  prompt.py  →  inference.py            │
│                                       └─ mlx-lm (warm)       │
└─────────────────────────────────────────────────────────────┘
```

State lives at `~/.termauto/`:

- `daemon.pid` — pid of the running process
- `daemon.json` — host/port/model in use
- `daemon.log` — uvicorn + inference logs

---

## Project layout

```
termauto/
├── install.sh
├── pyproject.toml
├── shell/
│   └── termauto.zsh           # the zle widget + precmd hook
└── src/termauto/
    ├── cli.py                 # `termauto` CLI entry point
    ├── daemon.py              # start/stop/status + foreground server
    ├── server.py              # FastAPI app
    ├── inference.py           # mlx-lm wrapper, model held warm
    └── prompt.py              # prompt template + candidate parsing
```

---

## Status

v0.1 — works, rough edges expected. Things on the immediate roadmap:

- `preexec` capture of stderr tail (currently we send exit codes only)
- Smarter candidate ranking (post-process: dedupe, score by buffer-prefix match)
- `--ghost` mode for inline single-suggestion rendering once trust is established
- bash + fish shell hooks
- LaunchAgent install for daemon auto-start

---

## License

MIT — see [LICENSE](./LICENSE).

Inspired by [cotabby](https://github.com/FuJacob/cotabby) (AGPL-3.0). termauto
shares no source code with cotabby; the design pattern (local-first LLM,
ghost/panel completion, on-device inference) was the inspiration.
