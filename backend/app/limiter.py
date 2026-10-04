"""Limit how many questions run at once; the others wait in line (and can be shown as "waiting")."""

from __future__ import annotations

import threading
from contextlib import contextmanager


class QueueTimeout(RuntimeError):
    """Waited too long for a free slot."""


class QuestionLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._sem = threading.BoundedSemaphore(limit)
        self._lock = threading.Lock()
        self.running = 0
        self.waiting = 0

    @contextmanager
    def slot(self, timeout: float):
        with self._lock:
            self.waiting += 1
        got = self._sem.acquire(timeout=timeout)
        with self._lock:
            self.waiting -= 1
            if got:
                self.running += 1
        if not got:
            raise QueueTimeout(f"{timeout:.0f}초 안에 처리 순서가 오지 않았습니다.")
        try:
            yield
        finally:
            with self._lock:
                self.running -= 1
            self._sem.release()

    def status(self) -> dict:
        with self._lock:
            return {"limit": self.limit, "running": self.running, "waiting": self.waiting}
