# termauto inside a prompt_toolkit CLI

Local-MLX ghost text inside any prompt_toolkit-based REPL or TUI — ipython,
ptpython, click-repl, pgcli, mycli, glances, sqlmap, your own thing.

## Quick start

```bash
# 1. termauto running locally (one-time)
pip install termauto
termauto start

# 2. Drop the single example file into your project
curl -O https://raw.githubusercontent.com/<your-fork>/termauto/main/examples/prompt_toolkit/termauto_suggest.py
```

In your code:

```python
from prompt_toolkit import PromptSession
from termauto_suggest import TermautoAutoSuggest

session = PromptSession(auto_suggest=TermautoAutoSuggest())
while True:
    line = session.prompt("> ")
    # ... handle line
```

That's it. Ghost text appears as you type. Right-arrow / End / Ctrl-F accepts.

## What this gives you

- **Same daemon, same model** as the zsh ghost-text strategy. No second
  process, no separate config.
- **Same safety guarantees:** the dangerous-command deny-list (rm -rf /,
  sudo rm -rf, git push --force, git reset --hard, etc.) is enforced on the
  daemon side before any text reaches your CLI.
- **Same failure mode:** if the daemon isn't running, you get one stderr
  warning and silent no-ops thereafter — no crashes, no spam.

## Adapting to your TUI framework

`termauto_suggest.py` is ~120 lines including comments. The shape generalizes
to any framework with an autosuggest/ghost-text hook:

1. **Implement the framework's autosuggest interface** (bubbletea has
   `tea.Cmd`, ink has render-time providers, ratatui exposes input-widget
   state, etc.).
2. **Inside that hook, POST to `http://127.0.0.1:8765/suggest_inline`** with
   `{buffer, cwd, recent_commands}` and `Accept: text/plain`.
3. **Enforce the prefix-match guard** — if the response doesn't start with
   the buffer, drop it. Don't let ghost text replace user input.
4. **Run the HTTP call off the UI thread** (async, goroutine, executor) so
   daemon latency doesn't freeze rendering.
5. **Treat failure modes as silent None**, with one stderr warning on first
   refused-connection to surface daemon-down to the user.

If you adapt this to another framework, please open an issue on the termauto
repo with what you needed. The intent is to grow the SDK shape from real
integrator pain, not speculative API design.

## Planned

A `pip install termauto-prompt-toolkit` package will eventually wrap this so
you don't have to copy-paste the file. For v0.2 the example file is the
contract — copy it, adapt it, file issues against it.

## Caveats

- **Apple Silicon only** (termauto runs MLX models).
- **First request after daemon start is 3-8s** — pre-warm with
  `curl -X POST http://127.0.0.1:8765/warmup` on your app's boot.
- **One model per daemon.** A Python REPL and a SQL REPL ideally want
  different models. Run two daemons on different ports if you need both,
  and pass `url=` to the constructor.
- **No streaming.** The daemon accumulates before responding (deny-list
  validation requires the full output). Typical wall time on
  Qwen3-1.7B-4bit: 250-450ms.
