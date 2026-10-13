"""Bounded in-process background task manager (no external queue).

Limitations (documented in docs/persistence.md): tasks live in this process only.  If the
backend stops, running tasks are lost; LangGraph checkpoints let a review be continued, but
no task is restarted automatically.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

log = logging.getLogger(__name__)


class TaskManager:
    def __init__(self, max_workers: int = 4) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="art-task")
        self._lock = threading.Lock()
        self._active: dict[str, Future[None]] = {}

    def submit(self, key: str, fn: Callable[[], None]) -> bool:
        """Run ``fn`` unless a task with ``key`` is already active.  Returns False for duplicates."""
        with self._lock:
            existing = self._active.get(key)
            if existing is not None and not existing.done():
                return False

            def runner() -> None:
                try:
                    fn()
                except Exception:  # last-resort guard: tasks must not die silently
                    log.exception("Background task %s crashed", key)
                finally:
                    with self._lock:
                        self._active.pop(key, None)

            self._active[key] = self._pool.submit(runner)
            return True

    def is_active(self, key: str) -> bool:
        with self._lock:
            fut = self._active.get(key)
            return fut is not None and not fut.done()

    def wait(self, key: str, timeout: float | None = None) -> None:
        with self._lock:
            fut = self._active.get(key)
        if fut is not None:
            fut.result(timeout=timeout)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
