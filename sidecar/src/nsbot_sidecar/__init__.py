"""Native runtime package for Rust <-> Python bridge."""

from __future__ import annotations

__all__ = ["CodeAgentRuntimeService", "RuntimeProcessError"]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from nsbot_sidecar.runtime.runtime_service import (
        CodeAgentRuntimeService,
        RuntimeProcessError,
    )

    exports = {
        "CodeAgentRuntimeService": CodeAgentRuntimeService,
        "RuntimeProcessError": RuntimeProcessError,
    }
    return exports[name]
