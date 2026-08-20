"""Conservative, opt-in idle lifecycle for the shared HTTP daemon."""

from __future__ import annotations

from contextlib import contextmanager
import os
import signal
import threading
import time
import uuid
from collections.abc import Callable, Iterator


class IdleLifecycle:
    """Track requests and renewable client leases before allowing idle exit."""

    def __init__(
        self,
        idle_seconds: float,
        *,
        lease_ttl: float = 90.0,
        clock: Callable[[], float] = time.monotonic,
        request_shutdown: Callable[[], None] | None = None,
    ) -> None:
        if idle_seconds <= 0 or lease_ttl <= 0:
            raise ValueError("idle and lease durations must be positive")
        self.idle_seconds = idle_seconds
        self.lease_ttl = lease_ttl
        self._clock = clock
        self._request_shutdown = request_shutdown or (
            lambda: os.kill(os.getpid(), signal.SIGTERM)
        )
        self._lock = threading.Lock()
        self._in_flight = 0
        self._last_activity = clock()
        self._leases: dict[str, float] = {}
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    @contextmanager
    def request(self) -> Iterator[None]:
        with self._lock:
            self._in_flight += 1
            self._last_activity = self._clock()
        try:
            yield
        finally:
            with self._lock:
                self._in_flight -= 1
                self._last_activity = self._clock()

    def acquire_lease(self) -> str:
        token = uuid.uuid4().hex
        self.renew_lease(token)
        return token

    def renew_lease(self, token: str) -> bool:
        if not token:
            return False
        with self._lock:
            self._leases[token] = self._clock() + self.lease_ttl
            self._last_activity = self._clock()
        return True

    def release_lease(self, token: str) -> None:
        with self._lock:
            self._leases.pop(token, None)
            self._last_activity = self._clock()

    def snapshot(self) -> dict:
        now = self._clock()
        with self._lock:
            self._leases = {key: expiry for key, expiry in self._leases.items() if expiry > now}
            return {
                "enabled": True,
                "idle_seconds": self.idle_seconds,
                "in_flight": self._in_flight,
                "active_leases": len(self._leases),
                "idle_for_seconds": max(0.0, now - self._last_activity),
            }

    def should_shutdown(self) -> bool:
        state = self.snapshot()
        return (
            state["in_flight"] == 0
            and state["active_leases"] == 0
            and state["idle_for_seconds"] >= self.idle_seconds
        )

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _monitor(self) -> None:
        interval = min(5.0, max(0.1, self.idle_seconds / 10))
        while not self._stopped.wait(interval):
            if self.should_shutdown():
                self._request_shutdown()
                return
