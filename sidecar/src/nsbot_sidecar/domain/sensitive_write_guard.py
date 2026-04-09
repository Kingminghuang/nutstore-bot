from __future__ import annotations

import re
from typing import Any


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

_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9._\-]{8,}"),
    re.compile(
        r"(?i)(?:api[_-]?key|authorization|token|secret(?:[_-]?value)?|password)\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{6,}"
    ),
)


def detect_sensitive_write_issues(payload: Any) -> list[str]:
    issues: list[str] = []
    _walk(payload, path=(), issues=issues)
    return issues


def _walk(value: Any, *, path: tuple[str, ...], issues: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key)
            key_norm = key_text.replace("-", "_").lower()
            next_path = path + (key_text,)
            if key_norm in _SENSITIVE_KEYS and not _is_allowed_sensitive_path(
                next_path
            ):
                if nested not in (None, ""):
                    issues.append(f"sensitive key at {'.'.join(next_path)}")
            _walk(nested, path=next_path, issues=issues)
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, path=path + (str(index),), issues=issues)
        return

    if isinstance(value, str):
        if _is_allowed_sensitive_path(path):
            return
        if any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS):
            issues.append(f"sensitive value pattern at {'.'.join(path)}")


def _is_allowed_sensitive_path(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    # `api_key_input` and `secret_value` are intentionally transient and moved to secret store.
    if path[-1] in {"api_key_input", "secret_value"}:
        return True
    return False
