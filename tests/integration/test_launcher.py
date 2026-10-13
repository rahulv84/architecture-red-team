"""Acceptance: `python src/run.py` starts both servers, serves health, and shuts both down cleanly."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1.5) as r:  # noqa: S310 - loopback
            return r.status == 200
    except OSError:
        return False


def _listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def test_run_py_starts_both_servers_and_stops_cleanly(tmp_path):
    api_port, ui_port = _free_port(), _free_port()
    env = {**os.environ, "FASTAPI_PORT": str(api_port), "STREAMLIT_PORT": str(ui_port), "LOCAL_DATA_DIR": str(tmp_path / "data"),
           "MOCK_LATENCY_SECONDS": "0"}
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # type: ignore[attr-defined]
    proc = subprocess.Popen([sys.executable, str(ROOT / "src" / "run.py"), "--no-browser"], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
    try:
        deadline = time.time() + 120
        api_ok = ui_ok = False
        while time.time() < deadline and not (api_ok and ui_ok):
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                pytest.fail(f"run.py exited early ({proc.returncode}):\n{out[-2000:]}")
            api_ok = _up(f"http://127.0.0.1:{api_port}/api/v1/health")
            ui_ok = _up(f"http://127.0.0.1:{ui_port}/_stcore/health")
            time.sleep(0.5)
        assert api_ok and ui_ok, "both services must become ready"
        assert (tmp_path / "data" / "uploads").is_dir()  # directories initialised by the launcher
        assert _up(f"http://127.0.0.1:{api_port}/docs")
        # Ctrl+C equivalent
        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)  # type: ignore[attr-defined]
        proc.wait(timeout=40)
        assert proc.returncode == 0
        time.sleep(0.5)
        assert not _listening(api_port) and not _listening(ui_port), "child processes must be terminated"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_port_conflict_gives_actionable_error(tmp_path):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        env = {**os.environ, "FASTAPI_PORT": str(port), "LOCAL_DATA_DIR": str(tmp_path / "data")}
        done = subprocess.run([sys.executable, str(ROOT / "src" / "run.py"), "--no-browser"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert done.returncode != 0
        assert f"Port {port}" in (done.stderr + done.stdout) and "FASTAPI_PORT" in (done.stderr + done.stdout)
    finally:
        blocker.close()
