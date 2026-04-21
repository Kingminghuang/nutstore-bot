from __future__ import annotations

import threading


class TurnCancellationRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}

    def create(self, turn_id: str) -> threading.Event:
        with self._lock:
            event = threading.Event()
            self._events[turn_id] = event
            return event

    def get(self, turn_id: str) -> threading.Event | None:
        with self._lock:
            return self._events.get(turn_id)

    def cancel(self, turn_id: str) -> bool:
        with self._lock:
            event = self._events.get(turn_id)
            if event is None:
                return False
            event.set()
            return True

    def clear(self, turn_id: str) -> None:
        with self._lock:
            self._events.pop(turn_id, None)
