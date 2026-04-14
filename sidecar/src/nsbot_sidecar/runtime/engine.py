from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, TypedDict

from nsbot_sidecar.runtime.runtime_service import (
    AgentRuntimeService,
    RunMetadata,
    RuntimeWorkerConfig,
)


class RuntimeResult(TypedDict):
    deltas: list[dict[str, Any]]
    final_answer: str | None
    session_messages: list[dict[str, Any]]
    timeline_entries: list[dict[str, Any]]


@dataclass(frozen=True)
class RuntimeRequestContext:
    run_id: str
    user_input: str
    auth_context: dict[str, Any]
    metadata: RunMetadata


class RuntimeEngine(Protocol):
    def process(
        self,
        run_id: str,
        user_input: str,
        auth_context: dict[str, Any],
        metadata: RunMetadata,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        permission_requester: Callable[[dict[str, Any]], str] | None = None,
    ) -> RuntimeResult: ...


class SmolagentsRuntimeEngine:
    def __init__(
        self,
        config: RuntimeWorkerConfig,
        *,
        model_factory=None,
        consolidator_factory=None,
    ):
        self._runtime_service = AgentRuntimeService(
            config,
            model_factory=model_factory,
            consolidator_factory=consolidator_factory,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runtime_service, name)

    def process(
        self,
        run_id: str,
        user_input: str,
        auth_context: dict[str, Any],
        metadata: RunMetadata,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        permission_requester: Callable[[dict[str, Any]], str] | None = None,
    ) -> RuntimeResult:
        return self._runtime_service.process(
            run_id=run_id,
            user_input=user_input,
            auth_context=auth_context,
            metadata=metadata,
            event_callback=event_callback,
            is_cancelled=is_cancelled,
            permission_requester=permission_requester,
        )


def create_runtime_engine(
    config: RuntimeWorkerConfig,
    *,
    model_factory=None,
    consolidator_factory=None,
) -> RuntimeEngine:
    return SmolagentsRuntimeEngine(
        config,
        model_factory=model_factory,
        consolidator_factory=consolidator_factory,
    )
