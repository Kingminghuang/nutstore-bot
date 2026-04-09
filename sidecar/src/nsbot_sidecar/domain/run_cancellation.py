from __future__ import annotations

import threading


class RunCancellationRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}

    def create(self, run_id: str) -> threading.Event:
        with self._lock:
            event = threading.Event()
            self._events[run_id] = event
            return event

    def get(self, run_id: str) -> threading.Event | None:
        with self._lock:
            return self._events.get(run_id)

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            event = self._events.get(run_id)
            if event is None:
                return False
            event.set()
            return True

    def clear(self, run_id: str) -> None:
        with self._lock:
            self._events.pop(run_id, None)
