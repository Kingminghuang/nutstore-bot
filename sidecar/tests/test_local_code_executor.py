from __future__ import annotations

import unittest

from nsbot_sidecar.runtime.local_code_executor import LocalCodeExecutor


class LocalCodeExecutorTests(unittest.TestCase):
    def test_executor_preserves_state_within_run(self) -> None:
        executor = LocalCodeExecutor(
            turn_id="turn-1",
            workspace_path="/tmp",
            timeout_seconds=5,
        )

        first = executor("counter = 1\ncounter")
        second = executor("counter += 1\ncounter")

        self.assertEqual(first.output, 1)
        self.assertEqual(second.output, 2)


if __name__ == "__main__":
    unittest.main()
