from __future__ import annotations

import unittest

from nsbot_sidecar.domain.sensitive_write_guard import detect_sensitive_write_issues


class SensitiveWriteGuardTests(unittest.TestCase):
    def test_detects_sensitive_value_patterns_in_non_secret_fields(self) -> None:
        payload = {
            "connection_data": {
                "display_name": "Team apiKey=sk-live-abc12345",
            },
            "models": [],
            "headers": [],
        }

        issues = detect_sensitive_write_issues(payload)

        self.assertTrue(issues)
        self.assertTrue(
            any("connection_data.display_name" in issue for issue in issues)
        )

    def test_allows_expected_transient_secret_fields(self) -> None:
        payload = {
            "connection_data": {
                "api_key_input": "sk-live-abc12345",
            },
            "models": [],
            "headers": [
                {
                    "name": "X-Token",
                    "value_kind": "secret",
                    "secret_value": "top-secret",
                }
            ],
        }

        issues = detect_sensitive_write_issues(payload)

        self.assertEqual(issues, [])
