from __future__ import annotations

from typing import Any, Callable

from nsbot_sidecar.runtime.engine import create_runtime_engine
from nsbot_sidecar.runtime.types import RunMetadata, RuntimeWorkerConfig


def execute_runtime_turn(
    config: RuntimeWorkerConfig,
    turn_id: str,
    user_input: str,
    auth_context: dict[str, Any],
    metadata: RunMetadata,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Forward runtime execution through RuntimeEngine."""
    engine = create_runtime_engine(config)
    return engine.process(
        turn_id=turn_id,
        user_input=user_input,
        auth_context=auth_context,
        metadata=metadata,
        event_callback=event_callback,
        is_cancelled=is_cancelled,
    )
