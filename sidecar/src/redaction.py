from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any


REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "secretvalue",
    "secret_value",
    "token",
}
_KEY_VALUE_PATTERN = re.compile(
    r'(?i)("?(?:api[_-]?key|authorization|token|secret(?:[_-]?value)?|password)"?\s*[:=]\s*)(".*?"|\'.*?\'|[^\s,}\]]+)'
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+")
_SK_PATTERN = re.compile(r"\bsk-[A-Za-z0-9._\-]+")


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).replace("-", "_").lower()
            if normalized_key in _SENSITIVE_KEYS:
                result[str(key)] = REDACTED
            else:
                result[str(key)] = redact_sensitive(item)
        return result
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


def redact_text(text: str) -> str:
    redacted = _KEY_VALUE_PATTERN.sub(
        lambda match: f'{match.group(1)}"{REDACTED}"', text
    )
    redacted = _BEARER_PATTERN.sub(f"Bearer {REDACTED}", redacted)
    return _SK_PATTERN.sub(REDACTED, redacted)


class RedactingLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_sensitive(record.msg)
        record.args = redact_sensitive(record.args)
        return True


def install_log_redaction_filter() -> None:
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if any(
            isinstance(existing, RedactingLogFilter) for existing in handler.filters
        ):
            continue
        handler.addFilter(RedactingLogFilter())
