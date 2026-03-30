from __future__ import annotations

import unittest

from python_runtime.run_events import (
    completed_event,
    delta_event,
    failed_event,
    status_event,
    timeline_entry_event,
)


class RunEventTests(unittest.TestCase):
    def test_status_event_shape(self) -> None:
        envelope = status_event(
            run_id="run_1",
            session_id="sess_1",
            sequence=1,
            created_at="2026-03-24T12:00:00Z",
            status="running",
            message="Run started",
        )

        self.assertEqual(envelope.event, "run.status")
        self.assertEqual(envelope.id, "run_1:1")
        self.assertEqual(envelope.data["status"], "running")

    def test_delta_step_and_terminal_events(self) -> None:
        delta = delta_event(
            run_id="run_1",
            session_id="sess_1",
            sequence=2,
            created_at="2026-03-24T12:00:01Z",
            step_id="step-1",
            text="partial output",
        )
        step = timeline_entry_event(
            run_id="run_1",
            session_id="sess_1",
            sequence=3,
            created_at="2026-03-24T12:00:02Z",
            entry={
                "id": "tle_1",
                "entryKind": "action",
                "displayRole": "assistant",
                "stepNumber": 1,
                "contentJson": {
                    "codeAction": "print('hello')",
                    "actionOutput": {"status": "ok"},
                    "observations": ["changed file"],
                },
            },
        )
        completed = completed_event(
            run_id="run_1",
            session_id="sess_1",
            sequence=4,
            created_at="2026-03-24T12:00:03Z",
            final_answer="Done",
        )
        failed = failed_event(
            run_id="run_2",
            session_id="sess_2",
            sequence=5,
            created_at="2026-03-24T12:00:04Z",
            error_code="provider_timeout",
            error_message="Provider timed out",
        )

        self.assertEqual(delta.data["text"], "partial output")
        self.assertEqual(step.data["entry"]["entryKind"], "action")
        self.assertEqual(step.data["entry"]["stepNumber"], 1)
        self.assertEqual(
            step.data["entry"]["contentJson"]["codeAction"], "print('hello')"
        )
        self.assertEqual(
            step.data["entry"]["contentJson"]["actionOutput"], {"status": "ok"}
        )
        self.assertEqual(completed.data["finalAnswer"], "Done")
        self.assertEqual(failed.data["errorCode"], "provider_timeout")


if __name__ == "__main__":
    unittest.main()
