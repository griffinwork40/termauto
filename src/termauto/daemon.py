"""Daemon lifecycle: start, stop, status, pidfile management.

Single-process daemon. PID + port written to ~/.termauto/. Hot-reload of the
model is a future concern; for now restart the daemon to change models.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from . import DEFAULT_HOST, DEFAULT_MODEL, DEFAULT_PORT

STATE_DIR = Path(os.environ.get("TERMAUTO_STATE_DIR", Path.home() / ".termauto"))
PID_FILE = STATE_DIR / "daemon.pid"
INFO_FILE = STATE_DIR / "daemon.json"
LOG_FILE = STATE_DIR / "daemon.log"


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def read_info() -> Optional[dict]:
    if not INFO_FILE.exists():
        return None
    try:
        return json.loads(INFO_FILE.read_text())
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def status() -> dict:
    """Return a snapshot of daemon state. Never raises."""
    info = read_info()
    if info is None:
        return {"running": False, "reason": "no pid file"}

    pid = info.get("pid")
    if pid is None or not _pid_alive(pid):
        return {"running": False, "reason": "stale pid", "info": info}

    # Try a health check
    url = f"http://{info.get('host', DEFAULT_HOST)}:{info.get('port', DEFAULT_PORT)}/healthz"
    try:
        r = httpx.get(url, timeout=1.0)
        if r.status_code == 200:
            return {"running": True, "info": info, "health": r.json()}
        return {"running": True, "info": info, "health_status": r.status_code}
    except httpx.HTTPError as e:
        return {"running": True, "info": info, "health_error": str(e)}


def start(
    model: str = DEFAULT_MODEL,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    foreground: bool = False,
    warmup: bool = True,
) -> dict:
    """Spawn the daemon. Returns status dict."""
    _ensure_state_dir()

    existing = status()
    if existing.get("running"):
        return {"already_running": True, **existing}

    # Clean stale state
    PID_FILE.unlink(missing_ok=True)
    INFO_FILE.unlink(missing_ok=True)

    if foreground:
        _run_foreground(model, host, port, warmup=warmup)
        return {"foreground": True}

    # Fork via subprocess so the parent can return cleanly
    log_fh = open(LOG_FILE, "ab", buffering=0)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "termauto.daemon",
            "--run",
            "--model", model,
            "--host", host,
            "--port", str(port),
            *(["--warmup"] if warmup else []),
        ],
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
        cwd=str(Path.home()),
    )

    # Wait up to 30s for /healthz to come up (model load can be slow first time)
    deadline = time.monotonic() + 30.0
    url = f"http://{host}:{port}/healthz"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return {
                "started": False,
                "reason": "daemon exited during startup",
                "exit_code": proc.returncode,
                "log_file": str(LOG_FILE),
            }
        try:
            r = httpx.get(url, timeout=0.5)
            if r.status_code == 200:
                INFO_FILE.write_text(json.dumps({
                    "pid": proc.pid,
                    "host": host,
                    "port": port,
                    "model": model,
                    "started_at": time.time(),
                }))
                PID_FILE.write_text(str(proc.pid))
                return {"started": True, "pid": proc.pid, "host": host, "port": port, "model": model}
        except httpx.HTTPError:
            pass
        time.sleep(0.5)

    # Timed out — daemon may still be loading the model. Record state anyway.
    INFO_FILE.write_text(json.dumps({
        "pid": proc.pid,
        "host": host,
        "port": port,
        "model": model,
        "started_at": time.time(),
        "health_timeout": True,
    }))
    PID_FILE.write_text(str(proc.pid))
    return {
        "started": True,
        "health_timeout": True,
        "pid": proc.pid,
        "host": host,
        "port": port,
        "model": model,
        "log_file": str(LOG_FILE),
    }


def stop(force: bool = False) -> dict:
    info = read_info()
    if info is None:
        return {"stopped": False, "reason": "not running"}

    pid = info.get("pid")
    if pid is None:
        INFO_FILE.unlink(missing_ok=True)
        PID_FILE.unlink(missing_ok=True)
        return {"stopped": False, "reason": "no pid in info"}

    if not _pid_alive(pid):
        INFO_FILE.unlink(missing_ok=True)
        PID_FILE.unlink(missing_ok=True)
        return {"stopped": True, "was": "stale"}

    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass

    # Wait up to 5s for it to exit
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            break
        time.sleep(0.1)

    INFO_FILE.unlink(missing_ok=True)
    PID_FILE.unlink(missing_ok=True)
    return {"stopped": True, "pid": pid, "force": force}


def _run_foreground(model: str, host: str, port: int, warmup: bool) -> None:
    """Actually run the server. Called from `python -m termauto.daemon --run`."""
    import logging

    import uvicorn

    from .inference import InferenceEngine
    from .server import create_app

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    engine = InferenceEngine(model_name=model)
    if warmup:
        engine.load()
        engine.warmup()

    app = create_app(engine)
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)


def _main() -> None:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--run", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--warmup", action="store_true")
    args = p.parse_args()

    if args.run:
        _run_foreground(args.model, args.host, args.port, warmup=args.warmup)
    else:
        # Direct invocation just starts foreground
        _run_foreground(args.model, args.host, args.port, warmup=args.warmup)


if __name__ == "__main__":
    _main()
