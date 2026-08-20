"""Explicit process lifecycle for the opt-in shared MCP daemon."""

import json
import fcntl
import os
import signal
import subprocess
import time
import urllib.request
import urllib.parse
from pathlib import Path


def paths(state_dir: Path) -> tuple[Path, Path]:
    return state_dir / "mindgraph-daemon.pid", state_dir / "mindgraph-daemon.log"


def lock_path(state_dir: Path) -> Path:
    return state_dir / "mindgraph-daemon.start.lock"


def identity_path(state_dir: Path) -> Path:
    return state_dir / "mindgraph-daemon.identity.json"


def read_identity(state_dir: Path) -> dict | None:
    try:
        payload = json.loads(identity_path(state_dir).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


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


def is_mindgraph_health(payload: dict | None) -> bool:
    return bool(
        payload
        and payload.get("status") == "ok"
        and isinstance(payload.get("scopes"), list)
    )


def status(state_dir: Path, health_url: str | None = None) -> dict:
    pid = read_pid(state_dir)
    tracked = alive(pid)
    observed = health(health_url) if health_url else None
    if is_mindgraph_health(observed):
        observed_pid = observed.get("pid")
        return {
            "status": "running",
            "pid": observed_pid,
            "supervision": "pid_file" if tracked and observed_pid == pid else "external",
            "pid_file_pid": pid,
            "health": observed,
        }
    result = {"status": "running" if tracked else "stopped", "pid": pid}
    if health_url:
        result["health"] = observed
    return result


def start(
    state_dir: Path,
    command: list[str],
    *,
    health_url: str | None = None,
    startup_timeout: float = 20.0,
) -> dict:
    state_dir.mkdir(parents=True, exist_ok=True)
    with lock_path(state_dir).open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = status(state_dir, health_url)
        if current["status"] == "running":
            return current
        pid_file, log_file = paths(state_dir)
        with log_file.open("ab") as log:
            proc = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True,
            )
        pid_file.write_text(f"{proc.pid}\n")
        identity_path(state_dir).write_text(json.dumps({
            "pid": proc.pid,
            "command": command,
        }) + "\n")
        if not health_url:
            return {"status": "started", "pid": proc.pid}
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            observed = health(health_url, timeout=0.25)
            if is_mindgraph_health(observed):
                return {"status": "started", "pid": proc.pid, "health": observed}
            if not alive(proc.pid):
                break
            time.sleep(0.1)
        return {
            "status": "start_failed",
            "pid": proc.pid,
            "health": health(health_url, timeout=0.25),
        }


def stop(
    state_dir: Path,
    timeout: float = 5.0,
    *,
    health_url: str | None = None,
) -> dict:
    pid = read_pid(state_dir)
    if health_url:
        observed = health(health_url)
        if is_mindgraph_health(observed) and observed.get("pid") is None:
            return {"status": "refused_unverified_listener", "pid": pid}
        observed_pid = observed.get("pid") if is_mindgraph_health(observed) else None
        if observed_pid is not None and observed_pid != pid:
            return {
                "status": "refused_pid_mismatch",
                "pid": pid,
                "observed_pid": observed_pid,
            }
    if not alive(pid):
        paths(state_dir)[0].unlink(missing_ok=True)
        identity_path(state_dir).unlink(missing_ok=True)
        return {"status": "stopped", "pid": pid}
    identity = read_identity(state_dir)
    if identity is None or identity.get("pid") != pid:
        return {"status": "refused_unverified_pid", "pid": pid}
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if alive(pid):
        return {"status": "stop_timeout", "pid": pid}
    paths(state_dir)[0].unlink(missing_ok=True)
    identity_path(state_dir).unlink(missing_ok=True)
    return {"status": "stopped", "pid": pid}


def health(url: str, timeout: float = 1.0) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception as exc:
        return {"status": "unhealthy", "error": str(exc)}


def health_url_for_mcp(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid MCP URL: {url}")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/health", "", ""))


def daemon_endpoint_args(url: str) -> tuple[str, int, str]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("auto-start requires a loopback http MCP URL")
    return parsed.hostname, parsed.port or 80, parsed.path or "/mcp"


def health_url(host: str, port: int) -> str:
    rendered_host = f"[{host}]" if ":" in host else host
    return f"http://{rendered_host}:{port}/health"


def idle_opt_in(state_dir: Path) -> float | None:
    """Return the explicitly activated idle grace, or None when disabled."""
    marker = state_dir / "idle-lifecycle.enabled"
    try:
        value = float(marker.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    return value if value >= 60 else None
