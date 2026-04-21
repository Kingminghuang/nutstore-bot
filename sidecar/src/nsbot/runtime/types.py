from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, TypedDict

from anyio.abc import ObjectReceiveStream


class RuntimeProcessError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class RuntimeCancelledError(RuntimeProcessError):
    def __init__(self, message: str = "Turn cancelled"):
        super().__init__("cancelled", message)


@dataclass(frozen=True)
class RuntimeWorkerConfig:
    model_id: str
    ns_bot_home: str
    workspace_path_default: str
    allow_console_output: bool = True
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    direct_reasoning_effort: str | None = None
    request_timeout_ms: int = 60_000
    fd_executable: str | None = None
    rg_executable: str | None = None
    tool_os_type: str | None = None
    max_steps: int = 20
    session_mode: str = "auto"


@dataclass(frozen=True)
class RunMetadata:
    workspace_path: str | None = None
    session_key: str | None = None


class RuntimeResult(TypedDict):
    deltas: list[dict[str, Any]]
    final_answer: str | None
    session_messages: list[dict[str, Any]]


class RuntimeEvent(TypedDict):
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class RuntimeEventStream:
    events: ObjectReceiveStream[RuntimeEvent]
    result: Awaitable[RuntimeResult]


@dataclass(frozen=True)
class RuntimeRequestContext:
    turn_id: str
    user_input: str
    images: list[str] | None
    auth_context: dict[str, Any]
    metadata: RunMetadata


class RuntimeEngine(Protocol):
    async def process_async(
        self,
        turn_id: str,
        user_input: str,
        auth_context: dict[str, Any],
        metadata: RunMetadata,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        permission_requester: Callable[[dict[str, Any]], str] | None = None,
        images: list[str] | None = None,
    ) -> RuntimeResult: ...

    async def process_stream_async(
        self,
        turn_id: str,
        user_input: str,
        auth_context: dict[str, Any],
        metadata: RunMetadata,
        is_cancelled: Callable[[], bool] | None = None,
        permission_requester: Callable[[dict[str, Any]], str] | None = None,
        images: list[str] | None = None,
    ) -> RuntimeEventStream: ...
