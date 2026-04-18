"""Native sidecar package for desktop bridge and runtime entry points."""

from __future__ import annotations

__all__ = ["RuntimeCancelledError", "RuntimeProcessError"]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from nsbot_sidecar.runtime.types import RuntimeCancelledError, RuntimeProcessError

    exports = {
        "RuntimeCancelledError": RuntimeCancelledError,
        "RuntimeProcessError": RuntimeProcessError,
    }
    return exports[name]
