#!/usr/bin/env python
"""One-command launcher for Architecture Red Team.

    python src/run.py            # starts FastAPI + Streamlit, opens the browser, Ctrl+C stops both
    python src/run.py --no-browser

Responsibilities: initialise local directories, start the API and UI as child processes,
wait for readiness, open the UI, supervise both processes and terminate them cleanly.
"""
from __future__ import annotations

import argparse
import os
import signal
import site
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

SRC = Path(__file__).resolve().parent  # importable packages: backend, frontend
ROOT = SRC.parent  # project root: .env, data/
MIN_PYTHON = (3, 11)
REQUIRED_MODULES = ("fastapi", "uvicorn", "streamlit", "langgraph", "pydantic", "sqlalchemy", "httpx")


def _load_env() -> None:
    """Load .env (without overriding real environment variables)."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _check_python_and_deps() -> None:
    if sys.version_info < MIN_PYTHON:
        sys.exit(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required (found {sys.version.split()[0]}).")
    missing = []
    for mod in REQUIRED_MODULES:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        sys.exit("Missing Python packages: " + ", ".join(missing) + "\nInstall them with:\n    pip install -r requirements.txt")


def _child_pythonpath() -> str:
    """``src`` + this interpreter's site-packages for the child processes.

    On Windows, a venv ``python.exe`` is a redirector to the base interpreter. Debuggers such as debugpy
    (VS Code F5) re-launch child Python processes in a way that can drop the venv, so ``-m uvicorn`` fails
    with "No module named uvicorn". Passing the parent's site-packages explicitly keeps the children on
    the same packages that ``_check_python_and_deps`` just verified.
    """
    return os.pathsep.join(dict.fromkeys([str(SRC), *site.getsitepackages()]))


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0 if os.name == "nt" else 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed loopback URL
            return 200 <= resp.status < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _wait_for(url: str, proc: subprocess.Popen[bytes], seconds: int, name: str, log: Path) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]) if log.exists() else ""
            sys.exit(f"{name} exited early (code {proc.returncode}). Last log lines:\n{tail}")
        if _http_ok(url):
            return
        time.sleep(0.4)
    sys.exit(f"{name} did not become ready within {seconds}s. See {log}.")


def _terminate(procs: list[tuple[str, subprocess.Popen[bytes]]]) -> None:
    for name, p in procs:
        if p.poll() is None:
            print(f"  stopping {name} ...")
            try:
                if os.name == "nt":
                    p.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    p.terminate()
            except (OSError, ValueError):
                p.terminate()
    deadline = time.time() + 10
    for _, p in procs:
        while p.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if p.poll() is None:
            p.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Start Architecture Red Team (FastAPI + Streamlit).")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    parser.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    args = parser.parse_args()

    _load_env()
    _check_python_and_deps()
    host = args.host or os.environ.get("BIND_HOST", "127.0.0.1")
    api_port = int(os.environ.get("FASTAPI_PORT", "8000"))
    ui_port = int(os.environ.get("STREAMLIT_PORT", "8501"))
    data_dir = Path(os.environ.get("LOCAL_DATA_DIR", "data"))
    data_dir = data_dir if data_dir.is_absolute() else ROOT / data_dir
    for sub in ("uploads", "reports", "snapshots", "checkpoints", "logs"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)

    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding to {host}. This POC has no authentication and must not be exposed publicly.")
    for label, port, var in (("FastAPI", api_port, "FASTAPI_PORT"), ("Streamlit", ui_port, "STREAMLIT_PORT")):
        if not _port_free(host, port):
            sys.exit(f"Port {port} ({label}) is already in use on {host}.\n"
                     f"Stop the other process or choose another port, e.g.  set {var}={port + 1}  (or edit .env).")

    api_url = f"http://{host}:{api_port}"
    ui_url = f"http://{host}:{ui_port}"
    env = {**os.environ, "ART_BACKEND_URL": f"{api_url}/api/v1", "PYTHONPATH": _child_pythonpath(), "PYTHONUNBUFFERED": "1",
           "FASTAPI_PORT": str(api_port), "STREAMLIT_PORT": str(ui_port)}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # type: ignore[attr-defined]
    logs = data_dir / "logs"
    procs: list[tuple[str, subprocess.Popen[bytes]]] = []

    def spawn(name: str, cmd: list[str], log: Path) -> subprocess.Popen[bytes]:
        handle = open(log, "wb")  # noqa: SIM115 - owned by the child for its lifetime
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT, creationflags=flags)
        procs.append((name, proc))
        return proc

    print("Architecture Red Team - starting local services ...")
    try:
        api = spawn("FastAPI", [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", host, "--port", str(api_port),
                                "--log-level", "warning"], logs / "backend.out.log")
        _wait_for(f"{api_url}/api/v1/health", api, 90, "FastAPI backend", logs / "backend.out.log")
        ui = spawn("Streamlit", [sys.executable, "-m", "streamlit", "run", str(SRC / "frontend" / "app.py"), "--server.port", str(ui_port),
                                 "--server.address", host, "--server.headless", "true", "--browser.gatherUsageStats", "false",
                                 "--server.fileWatcherType", "none"], logs / "streamlit.out.log")
        _wait_for(f"{ui_url}/_stcore/health", ui, 90, "Streamlit UI", logs / "streamlit.out.log")
    except SystemExit:
        _terminate(procs)
        raise
    except KeyboardInterrupt:
        _terminate(procs)
        return 130

    print()
    print("=" * 66)
    print("  Architecture Red Team is running")
    print(f"  Application : {ui_url}")
    print(f"  API         : {api_url}/api/v1   (Swagger: {api_url}/docs)")
    print(f"  Data        : {data_dir}")
    print("  Press Ctrl+C to stop both services.")
    print("=" * 66)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(ui_url)).start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    for sig_name in ("SIGTERM", "SIGBREAK"):  # SIGBREAK: Ctrl+Break / CTRL_BREAK_EVENT on Windows
        if hasattr(signal, sig_name):
            signal.signal(getattr(signal, sig_name), lambda *_: stop.set())
    exit_code = 0
    try:
        while not stop.is_set():
            for name, p in procs:
                if p.poll() is not None:
                    print(f"\n{name} stopped unexpectedly (exit code {p.returncode}); shutting down. See {logs}.")
                    exit_code = 1
                    stop.set()
            time.sleep(0.5)
    finally:
        print("\nShutting down ...")
        _terminate(procs)
        print("Stopped.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
