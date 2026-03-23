"""Compatibility package exposing sidecar modules under python_runtime.*."""

from __future__ import annotations

__all__ = ["CodeAgentRuntimeService", "RuntimeProcessError"]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from runtime_service import CodeAgentRuntimeService, RuntimeProcessError

    exports = {
        "CodeAgentRuntimeService": CodeAgentRuntimeService,
        "RuntimeProcessError": RuntimeProcessError,
    }
    return exports[name]
