from __future__ import annotations

import logging
import unittest

from nsbot_sidecar.api.redaction import REDACTED, RedactingLogFilter, redact_sensitive


class RedactionTests(unittest.TestCase):
    def test_redact_sensitive_masks_nested_sensitive_fields(self) -> None:
        payload = {
            "apiKey": "sk-live-123",
            "headers": {
                "Authorization": "Bearer token-abc",
                "nested": [{"secretValue": "secret-1"}],
            },
        }

        redacted = redact_sensitive(payload)

        self.assertEqual(redacted["apiKey"], REDACTED)
        self.assertEqual(redacted["headers"]["Authorization"], REDACTED)
        self.assertEqual(redacted["headers"]["nested"][0]["secretValue"], REDACTED)

    def test_log_filter_redacts_message_and_args(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='payload={"apiKey":"sk-secret"}',
            args=({"authorization": "Bearer abc"},),
            exc_info=None,
        )

        RedactingLogFilter().filter(record)

        self.assertEqual(record.msg, 'payload={"apiKey":"[REDACTED]"}')
        self.assertEqual(record.args, {"authorization": "[REDACTED]"})
