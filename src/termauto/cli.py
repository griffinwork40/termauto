"""termauto CLI — start/stop the daemon, query suggestions, install the shell hook."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import click
import httpx

from . import DEFAULT_HOST, DEFAULT_MODEL, DEFAULT_PORT, __version__
from . import daemon as daemon_mod


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="termauto")
def main() -> None:
    """termauto — local-first LLM shell completion."""


@main.command()
@click.option("--model", default=DEFAULT_MODEL, show_default=True, help="MLX model name or local path")
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option("--port", default=DEFAULT_PORT, show_default=True, type=int)
@click.option("--foreground/--background", default=False, help="Run in foreground (blocks).")
@click.option("--warmup/--no-warmup", default=True, help="Run a tiny warmup generation at startup.")
def start(model: str, host: str, port: int, foreground: bool, warmup: bool) -> None:
    """Start the daemon."""
    result = daemon_mod.start(model=model, host=host, port=port, foreground=foreground, warmup=warmup)
    click.echo(json.dumps(result, indent=2))
    if result.get("started") is False:
        sys.exit(1)


@main.command()
@click.option("--force", is_flag=True, help="SIGKILL instead of SIGTERM")
def stop(force: bool) -> None:
    """Stop the daemon."""
    result = daemon_mod.stop(force=force)
    click.echo(json.dumps(result, indent=2))


@main.command()
def status() -> None:
    """Show daemon status."""
    result = daemon_mod.status()
    click.echo(json.dumps(result, indent=2))


@main.command()
@click.option("--force", is_flag=True)
def restart(force: bool) -> None:
    """Stop then start the daemon (uses last-recorded model/host/port)."""
    info = daemon_mod.read_info() or {}
    daemon_mod.stop(force=force)
    result = daemon_mod.start(
        model=info.get("model", DEFAULT_MODEL),
        host=info.get("host", DEFAULT_HOST),
        port=info.get("port", DEFAULT_PORT),
    )
    click.echo(json.dumps(result, indent=2))


@main.command()
@click.argument("buffer", default="")
@click.option("--cwd", default=None, help="Override cwd (default: $PWD)")
@click.option("--n", "n_candidates", default=5, type=int, show_default=True)
@click.option("--stderr-tail", default=None, help="Last stderr tail to include")
@click.option("--recent", default=None, help="JSON array of [cmd, exit_code] pairs")
@click.option("--temperature", default=0.4, type=float, show_default=True)
@click.option("--max-tokens", default=256, type=int, show_default=True)
@click.option("--format", "fmt", type=click.Choice(["plain", "json", "lines"]), default="lines", show_default=True,
              help="plain=human-readable, json=full response, lines=one cmd per line (for shell widget)")
def suggest(
    buffer: str,
    cwd: Optional[str],
    n_candidates: int,
    stderr_tail: Optional[str],
    recent: Optional[str],
    temperature: float,
    max_tokens: int,
    fmt: str,
) -> None:
    """Ask the daemon for completions. Used both by humans and by the zsh widget."""
    info = daemon_mod.read_info()
    if info is None:
        click.echo("daemon not running. start it with: termauto start", err=True)
        sys.exit(2)

    host = info.get("host", DEFAULT_HOST)
    port = info.get("port", DEFAULT_PORT)

    payload: dict = {
        "cwd": cwd or os.environ.get("PWD") or os.getcwd(),
        "buffer": buffer,
        "n_candidates": n_candidates,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if stderr_tail:
        payload["last_stderr_tail"] = stderr_tail
    if recent:
        try:
            payload["recent_commands"] = json.loads(recent)
        except json.JSONDecodeError as e:
            click.echo(f"invalid --recent json: {e}", err=True)
            sys.exit(2)

    try:
        r = httpx.post(
            f"http://{host}:{port}/complete",
            json=payload,
            timeout=30.0,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        click.echo(f"daemon request failed: {e}", err=True)
        sys.exit(3)

    data = r.json()
    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
    elif fmt == "lines":
        for c in data["candidates"]:
            click.echo(c["cmd"])
    else:  # plain
        click.echo(f"# {data['model']}  ({data['elapsed_ms']:.0f}ms)")
        for i, c in enumerate(data["candidates"], 1):
            line = f"{i}. {c['cmd']}"
            if c.get("reason"):
                line += f"  # {c['reason']}"
            click.echo(line)


@main.command("install-shell")
@click.option("--zshrc", default=None, help="Path to .zshrc (default: $HOME/.zshrc)")
@click.option("--print-only", is_flag=True, help="Print the source line instead of modifying .zshrc")
def install_shell(zshrc: Optional[str], print_only: bool) -> None:
    """Install the zsh widget by sourcing termauto.zsh from your .zshrc."""
    pkg_dir = Path(__file__).resolve().parent
    # The shell file is shipped at <pkg>/shell/termauto.zsh (via package-data)
    # OR at <repo>/shell/termauto.zsh during development.
    candidates = [
        pkg_dir / "shell" / "termauto.zsh",
        pkg_dir.parent.parent / "shell" / "termauto.zsh",
        pkg_dir.parent.parent.parent / "shell" / "termauto.zsh",
    ]
    zsh_path = next((p for p in candidates if p.exists()), None)
    if zsh_path is None:
        click.echo("could not locate shell/termauto.zsh in package — is the install broken?", err=True)
        sys.exit(2)

    source_line = f"source {zsh_path}"
    marker = "# termauto shell hook"

    if print_only:
        click.echo(marker)
        click.echo(source_line)
        return

    target = Path(zshrc) if zshrc else Path.home() / ".zshrc"
    target.touch(exist_ok=True)
    content = target.read_text()
    if marker in content:
        click.echo(f"already installed in {target}")
        return

    with target.open("a") as f:
        f.write(f"\n{marker}\n{source_line}\n")
    click.echo(f"appended termauto hook to {target}")
    click.echo("open a new shell, or run: source ~/.zshrc")


if __name__ == "__main__":
    main()
