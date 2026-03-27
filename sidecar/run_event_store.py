from __future__ import annotations

from dataclasses import dataclass, field
import threading

from run_events import RunEventEnvelope


@dataclass
class _RunEventState:
    envelopes: list[RunEventEnvelope] = field(default_factory=list)
    closed: bool = False


class RunEventStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, _RunEventState] = {}

    def append(
        self, run_id: str, envelope: RunEventEnvelope, *, terminal: bool = False
    ) -> None:
        with self._lock:
            state = self._states.setdefault(run_id, _RunEventState())
            state.envelopes.append(envelope)
            if terminal:
                state.closed = True

    def list_after(self, run_id: str, after_sequence: int) -> list[RunEventEnvelope]:
        with self._lock:
            state = self._states.get(run_id)
            if state is None:
                return []
            return [
                envelope
                for envelope in state.envelopes
                if int(envelope.data.get("sequence", 0)) > after_sequence
            ]

    def is_closed(self, run_id: str) -> bool:
        with self._lock:
            state = self._states.get(run_id)
            if state is None:
                return False
            return state.closed

    def clear(self, run_id: str) -> None:
        with self._lock:
            self._states.pop(run_id, None)

    def clear_many(self, run_ids: list[str]) -> None:
        with self._lock:
            for run_id in run_ids:
                self._states.pop(run_id, None)
