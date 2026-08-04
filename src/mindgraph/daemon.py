"""Explicit process lifecycle for the opt-in shared MCP daemon."""

import json
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path


def paths(state_dir: Path) -> tuple[Path, Path]:
    return state_dir / "mindgraph-daemon.pid", state_dir / "mindgraph-daemon.log"


def read_pid(state_dir: Path) -> int | None:
    try:
        return int(paths(state_dir)[0].read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        waited, _status = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def status(state_dir: Path) -> dict:
    pid = read_pid(state_dir)
    return {"status": "running" if alive(pid) else "stopped", "pid": pid}


def start(state_dir: Path, command: list[str]) -> dict:
    current = status(state_dir)
    if current["status"] == "running":
        return current
    state_dir.mkdir(parents=True, exist_ok=True)
    pid_file, log_file = paths(state_dir)
    with log_file.open("ab") as log:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=log, start_new_session=True)
    pid_file.write_text(f"{proc.pid}\n")
    return {"status": "started", "pid": proc.pid}


def stop(state_dir: Path, timeout: float = 5.0) -> dict:
    pid = read_pid(state_dir)
    if not alive(pid):
        paths(state_dir)[0].unlink(missing_ok=True)
        return {"status": "stopped", "pid": pid}
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if alive(pid):
        return {"status": "stop_timeout", "pid": pid}
    paths(state_dir)[0].unlink(missing_ok=True)
    return {"status": "stopped", "pid": pid}


def health(url: str, timeout: float = 1.0) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception as exc:
        return {"status": "unhealthy", "error": str(exc)}
