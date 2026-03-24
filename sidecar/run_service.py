from __future__ import annotations

from dataclasses import dataclass
import inspect
import logging
import threading
from typing import Any, Callable, Literal, cast

from fastapi import BackgroundTasks, HTTPException, status

from run_cancellation import RunCancellationRegistry
from run_event_store import RunEventStore
from run_events import (
    RunUsage,
    completed_event,
    delta_event,
    failed_event,
    message_event,
    replay_ready_event,
    status_event,
    step_event,
)
from local_paths import nsbot_home
from provider_catalog import list_providers
from repositories import (
    ProviderConnectionsRepository,
    RunsRepository,
    SessionsRepository,
    WorkspacesRepository,
    now_iso_timestamp,
)
from runtime_service import (
    CodeAgentRuntimeService,
    RuntimeCancelledError,
    RunMetadata,
    RuntimeProcessError,
    RuntimeWorkerConfig,
)
from secret_store import LocalSecretStore
from session_service import SessionService, serialize_message, serialize_session


RuntimeExecutor = Callable[..., dict[str, Any]]
RunLauncher = Callable[[Callable[[], None]], None]


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunValidationError(Exception):
    code: str
    detail: str
    status_code: int = status.HTTP_400_BAD_REQUEST


@dataclass(frozen=True)
class RunRequestFailed(Exception):
    status_code: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class RunService:
    workspaces: WorkspacesRepository
    sessions: SessionsRepository
    providers: ProviderConnectionsRepository
    runs: RunsRepository
    session_service: SessionService
    secret_store: LocalSecretStore
    event_store: RunEventStore | None = None
    cancellation_registry: RunCancellationRegistry | None = None
    ns_bot_home: str | None = None
    runtime_executor: RuntimeExecutor | None = None
    run_launcher: RunLauncher | None = None

    def create_run(
        self, payload: dict[str, Any], background_tasks: BackgroundTasks | None = None
    ) -> dict[str, Any]:
        session_id = _normalize_required_string(
            payload.get("sessionId", payload.get("session_id")),
            detail="Session id is required",
        )
        workspace_id = _normalize_required_string(
            payload.get("workspaceId", payload.get("workspace_id")),
            detail="Workspace id is required",
        )
        connection_id = _normalize_required_string(
            payload.get("connectionId", payload.get("connection_id")),
            detail="Connection id is required",
        )
        model_id = _normalize_required_string(
            payload.get("modelId", payload.get("model_id")),
            detail="Model id is required",
        )
        input_text = _normalize_required_string(
            payload.get("input", payload.get("inputText", payload.get("input_text"))),
            detail="Run input is required",
        )

        workspace = self._get_workspace_or_404(workspace_id)
        session = self._get_session_or_404(session_id)
        if session.workspace_id != workspace.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session does not belong to workspace",
            )

        bundle = self.providers.get_bundle_by_id(connection_id)
        if bundle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider connection not found",
            )

        run = self.runs.create(
            session_id=session_id,
            workspace_id=workspace_id,
            connection_id=connection_id,
            model_id=model_id,
            input_text=input_text,
        )

        user_message = self.session_service.append_message(
            session_id,
            {
                "role": "user",
                "content": input_text,
                "runId": run.id,
                "connectionId": connection_id,
                "modelId": model_id,
            },
        )
        self._append_event(
            run.id,
            status_event(
                run_id=run.id,
                session_id=session_id,
                sequence=1,
                created_at=run.created_at,
                status="queued",
                message="Run queued",
            ),
        )
        self._append_event(
            run.id,
            message_event(
                run_id=run.id,
                session_id=session_id,
                sequence=self._next_sequence(run.id),
                created_at=user_message["createdAt"],
                message_id=user_message["id"],
                role="user",
                content=user_message["content"],
                step_id=user_message.get("stepId"),
            ),
        )

        try:
            if not bundle.connection.is_enabled:
                raise RunValidationError(
                    code="provider_disabled",
                    detail="Provider connection is disabled",
                )

            self._ensure_model_allowed(bundle, model_id)

            secret_payload = self.secret_store.load_provider_secret(
                bundle.connection.secret_ref
            )
            api_key = secret_payload.api_key if secret_payload is not None else None
            if api_key is None or api_key.strip() == "":
                raise RunValidationError(
                    code="missing_api_key",
                    detail="Provider connection is missing an API key",
                )

            config = RuntimeWorkerConfig(
                model_id=model_id,
                ns_bot_home=str(nsbot_home(self.ns_bot_home)),
                workspace_path_default=workspace.real_path,
                direct_provider=bundle.connection.runtime_provider,
                direct_base_url=bundle.connection.base_url,
                direct_api_key=api_key,
                direct_model_id=model_id,
            )
            metadata = RunMetadata(
                workspace_path=workspace.real_path,
                session_key=session.id,
            )
            auth_context = {
                "uid": "local-user",
                "tid": "local-team",
                "exp_epoch": 0,
            }
        except RunValidationError as exc:
            payload = self._record_failed_run(
                run_id=run.id,
                session_id=session_id,
                connection_id=connection_id,
                model_id=model_id,
                error_code=exc.code,
                error_message=exc.detail,
            )
            raise RunRequestFailed(
                status_code=exc.status_code, payload=payload
            ) from exc

        self._start_run_thread(
            run_id=run.id,
            session_id=session_id,
            connection_id=connection_id,
            model_id=model_id,
            input_text=input_text,
            config=config,
            auth_context=auth_context,
            metadata=metadata,
        )

        current_run = self.runs.get_by_id(run.id)
        current_session = self.sessions.get_by_id(session_id)
        current_messages = [
            serialize_message(message)
            for message in self.session_service.messages.list_by_session_id(session_id)
        ]
        assistant_message = next(
            (
                message
                for message in reversed(current_messages)
                if message.get("runId") == run.id and message.get("role") == "assistant"
            ),
            None,
        )

        return {
            "run": serialize_run(current_run),
            "session": serialize_session(current_session),
            "messages": current_messages,
            "assistantMessage": assistant_message,
            "result": {
                "deltas": [],
                "steps": [],
                "finalAnswer": None,
            },
        }

    def _start_run_thread(
        self,
        *,
        run_id: str,
        session_id: str,
        connection_id: str,
        model_id: str,
        input_text: str,
        config: RuntimeWorkerConfig,
        auth_context: dict[str, Any],
        metadata: RunMetadata,
    ) -> None:
        cancellation_event = self._create_cancellation_event(run_id)
        task = lambda: self._execute_run_in_background(
            run_id=run_id,
            session_id=session_id,
            connection_id=connection_id,
            model_id=model_id,
            input_text=input_text,
            config=config,
            auth_context=auth_context,
            metadata=metadata,
            cancellation_event=cancellation_event,
        )
        if self.run_launcher is not None:
            self.run_launcher(task)
            return

        thread = threading.Thread(target=task, daemon=True)
        thread.start()

    def _execute_run_in_background(
        self,
        *,
        run_id: str,
        session_id: str,
        connection_id: str,
        model_id: str,
        input_text: str,
        config: RuntimeWorkerConfig,
        auth_context: dict[str, Any],
        metadata: RunMetadata,
        cancellation_event,
    ) -> None:
        started_at = now_iso_timestamp()
        self.runs.update(
            run_id,
            status="running",
            final_answer=None,
            error_code=None,
            error_message=None,
            started_at=started_at,
            completed_at=None,
        )
        self._append_event(
            run_id,
            status_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=started_at,
                status="running",
                message="Run started",
            ),
        )

        try:
            result, emitted_live_events = self._execute_runtime_run(
                config,
                run_id,
                input_text,
                auth_context,
                metadata,
                event_callback=lambda event: self._handle_runtime_event(
                    run_id, session_id, started_at, event
                ),
                is_cancelled=cancellation_event.is_set,
            )
        except RuntimeCancelledError:
            self._record_cancelled_run(
                run_id=run_id,
                session_id=session_id,
                connection_id=connection_id,
                model_id=model_id,
                started_at=started_at,
            )
            self._clear_cancellation_event(run_id)
            return
        except RuntimeProcessError as exc:
            if exc.code == "cancelled":
                self._record_cancelled_run(
                    run_id=run_id,
                    session_id=session_id,
                    connection_id=connection_id,
                    model_id=model_id,
                    started_at=started_at,
                )
                self._clear_cancellation_event(run_id)
                return
            self._record_failed_run(
                run_id=run_id,
                session_id=session_id,
                connection_id=connection_id,
                model_id=model_id,
                error_code=exc.code,
                error_message=exc.message,
                started_at=started_at,
            )
            self._clear_cancellation_event(run_id)
            return

        final_answer = str(result.get("final_answer") or "").strip() or "Completed."
        if not emitted_live_events:
            self._emit_runtime_events(run_id, session_id, result, started_at)

        assistant_message = self.session_service.append_message(
            session_id,
            {
                "role": "assistant",
                "content": final_answer,
                "runId": run_id,
                "connectionId": connection_id,
                "modelId": model_id,
            },
        )
        completed_run = self.runs.update(
            run_id,
            status="completed",
            final_answer=final_answer,
            error_code=None,
            error_message=None,
            started_at=started_at,
            completed_at=now_iso_timestamp(),
        )
        self._append_event(
            run_id,
            status_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=completed_run.completed_at or completed_run.updated_at,
                status="completed",
                message="Run completed",
            ),
        )
        self._append_event(
            run_id,
            message_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=assistant_message["createdAt"],
                message_id=assistant_message["id"],
                role="assistant",
                content=assistant_message["content"],
                step_id=assistant_message.get("stepId"),
            ),
        )
        self._append_event(
            run_id,
            completed_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=completed_run.completed_at or completed_run.updated_at,
                final_answer=final_answer,
            ),
        )
        self._append_event(
            run_id,
            replay_ready_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=completed_run.completed_at or completed_run.updated_at,
                last_event_sequence=self._last_sequence(run_id),
            ),
            terminal=True,
        )
        self._clear_cancellation_event(run_id)

    def _execute_runtime_run(
        self,
        config: RuntimeWorkerConfig,
        run_id: str,
        user_input: str,
        auth_context: dict[str, Any],
        metadata: RunMetadata,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        executor = self.runtime_executor or execute_runtime_run
        signature = inspect.signature(executor)
        executor_any = cast(Any, executor)
        supports_event_callback = (
            "event_callback" in signature.parameters
            or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            or any(
                parameter.kind == inspect.Parameter.VAR_POSITIONAL
                for parameter in signature.parameters.values()
            )
            or len(signature.parameters) >= 6
        )
        if "event_callback" in signature.parameters:
            return (
                executor_any(
                    config,
                    run_id,
                    user_input,
                    auth_context,
                    metadata,
                    event_callback=event_callback,
                    is_cancelled=is_cancelled,
                ),
                True,
            )
        if supports_event_callback:
            return (
                executor_any(
                    config,
                    run_id,
                    user_input,
                    auth_context,
                    metadata,
                    event_callback,
                    is_cancelled,
                ),
                True,
            )
        return executor_any(config, run_id, user_input, auth_context, metadata), False

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self.runs.get_by_id(run_id)
        if run.status in {"completed", "failed", "cancelled"}:
            return {"run": serialize_run(run), "cancelled": run.status == "cancelled"}

        if not self._cancel_run_event(run_id):
            raise HTTPException(status_code=404, detail="Run not found")

        updated_run = self.runs.update(
            run_id,
            status="cancelled",
            final_answer=run.final_answer,
            error_code="cancelled",
            error_message="Run cancellation requested",
            started_at=run.started_at,
            completed_at=run.completed_at,
        )
        self._append_event(
            run_id,
            status_event(
                run_id=run.id,
                session_id=run.session_id,
                sequence=self._next_sequence(run_id),
                created_at=updated_run.updated_at,
                status="cancelled",
                message="Run cancellation requested",
            ),
        )
        return {"run": serialize_run(updated_run), "cancelled": True}

    def _ensure_model_allowed(self, bundle, model_id: str) -> None:
        connection = bundle.connection
        if connection.kind == "custom":
            allowed = {
                model.model_id
                for model in bundle.models
                if model.source == "custom" and model.enabled
            }
            if model_id in allowed:
                return
        else:
            catalog_by_id = {provider["id"]: provider for provider in list_providers()}
            provider_id = connection.catalog_provider_id or ""
            catalog_entry = catalog_by_id.get(provider_id)
            catalog_models = {
                str(model.get("id") or "")
                for model in (catalog_entry or {}).get("models", [])
            }
            if connection.model_policy == "restricted":
                allowed = {
                    model.model_id
                    for model in bundle.models
                    if model.source == "catalog" and model.enabled
                }
            else:
                allowed = catalog_models
            if model_id in allowed:
                return

        raise RunValidationError(
            code="invalid_model",
            detail="Model is not available for this provider connection",
        )

    def _record_failed_run(
        self,
        *,
        run_id: str,
        session_id: str,
        connection_id: str,
        model_id: str,
        error_code: str,
        error_message: str,
        started_at: str | None = None,
    ) -> dict[str, Any]:
        failed_run = self.runs.update(
            run_id,
            status="failed",
            final_answer=None,
            error_code=error_code,
            error_message=error_message,
            started_at=started_at,
            completed_at=now_iso_timestamp(),
        )
        self.session_service.append_message(
            session_id,
            {
                "role": "system",
                "content": f"Run failed: {error_message}",
                "runId": run_id,
                "connectionId": connection_id,
                "modelId": model_id,
            },
        )
        current_session = self.sessions.get_by_id(session_id)
        LOGGER.warning(
            "Run failed: run_id=%s session_id=%s workspace_id=%s connection_id=%s model_id=%s error_code=%s error_message=%s",
            run_id,
            session_id,
            current_session.workspace_id,
            connection_id,
            model_id,
            error_code,
            error_message,
        )
        system_message = self.session_service.messages.list_by_session_id(session_id)[
            -1
        ]
        self._append_event(
            run_id,
            status_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=system_message.created_at,
                status="failed",
                message=error_message,
            ),
        )
        self._append_event(
            run_id,
            failed_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=system_message.created_at,
                error_code=error_code,
                error_message=error_message,
            ),
        )
        self._append_event(
            run_id,
            message_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=system_message.created_at,
                message_id=system_message.id,
                role="system",
                content=system_message.content,
                step_id=system_message.step_id,
            ),
        )
        self._append_event(
            run_id,
            replay_ready_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=system_message.created_at,
                last_event_sequence=self._last_sequence(run_id),
            ),
            terminal=True,
        )
        return {
            "detail": error_message,
            "run": serialize_run(failed_run),
            "session": serialize_session(current_session),
            "messages": [
                serialize_message(message)
                for message in self.session_service.messages.list_by_session_id(
                    session_id
                )
            ],
            "result": {
                "deltas": [],
                "steps": [],
                "finalAnswer": None,
            },
        }

    def _record_cancelled_run(
        self,
        *,
        run_id: str,
        session_id: str,
        connection_id: str,
        model_id: str,
        started_at: str | None = None,
    ) -> dict[str, Any]:
        cancelled_run = self.runs.update(
            run_id,
            status="cancelled",
            final_answer=None,
            error_code="cancelled",
            error_message="Run cancelled",
            started_at=started_at,
            completed_at=now_iso_timestamp(),
        )
        system_message = self.session_service.append_message(
            session_id,
            {
                "role": "system",
                "content": "Run cancelled",
                "runId": run_id,
                "connectionId": connection_id,
                "modelId": model_id,
            },
        )
        self._append_event(
            run_id,
            status_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=system_message["createdAt"],
                status="cancelled",
                message="Run cancelled",
            ),
        )
        self._append_event(
            run_id,
            message_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=system_message["createdAt"],
                message_id=system_message["id"],
                role="system",
                content=system_message["content"],
                step_id=system_message.get("stepId"),
            ),
        )
        self._append_event(
            run_id,
            replay_ready_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=system_message["createdAt"],
                last_event_sequence=self._last_sequence(run_id),
            ),
            terminal=True,
        )
        return {"run": serialize_run(cancelled_run), "cancelled": True}

    def list_run_events(self, run_id: str, after_sequence: int = 0):
        if self.event_store is None:
            return []
        return self.event_store.list_after(run_id, after_sequence)

    def is_run_event_stream_closed(self, run_id: str) -> bool:
        if self.event_store is None:
            return False
        return self.event_store.is_closed(run_id)

    def _emit_runtime_events(
        self, run_id: str, session_id: str, result: dict[str, Any], created_at: str
    ) -> None:
        for delta in result.get("deltas", []):
            self._append_event(
                run_id,
                delta_event(
                    run_id=run_id,
                    session_id=session_id,
                    sequence=self._next_sequence(run_id),
                    created_at=created_at,
                    step_id=str(delta.get("step_id") or "step-unknown"),
                    text=str(delta.get("text") or ""),
                ),
            )

        for step in result.get("steps", []):
            usage = step.get("usage") or {}
            self._append_event(
                run_id,
                step_event(
                    run_id=run_id,
                    session_id=session_id,
                    sequence=self._next_sequence(run_id),
                    created_at=created_at,
                    step_id=str(step.get("step_id") or "step-unknown"),
                    step_kind=_normalize_step_kind(step.get("step_kind")),
                    model_output=str(step.get("model_output") or ""),
                    observations=[
                        str(item) for item in (step.get("observations") or [])
                    ],
                    error=None if step.get("error") is None else str(step.get("error")),
                    usage=RunUsage(
                        input_tokens=_to_int(usage.get("input_tokens", 0)),
                        output_tokens=_to_int(usage.get("output_tokens", 0)),
                        reasoning_tokens=_to_int(usage.get("reasoning_tokens", 0)),
                    ),
                    duration_ms=_to_int(step.get("duration_ms", 0)),
                    has_delta=bool(step.get("has_delta")),
                ),
            )

    def _handle_runtime_event(
        self,
        run_id: str,
        session_id: str,
        created_at: str,
        event: dict[str, Any],
    ) -> None:
        event_type = str(event.get("type") or "")
        payload = event.get("payload") or {}
        if event_type == "delta":
            self._append_event(
                run_id,
                delta_event(
                    run_id=run_id,
                    session_id=session_id,
                    sequence=self._next_sequence(run_id),
                    created_at=created_at,
                    step_id=str(payload.get("step_id") or "step-unknown"),
                    text=str(payload.get("text") or ""),
                ),
            )
            return

        if event_type == "step":
            usage = payload.get("usage") or {}
            self._append_event(
                run_id,
                step_event(
                    run_id=run_id,
                    session_id=session_id,
                    sequence=self._next_sequence(run_id),
                    created_at=created_at,
                    step_id=str(payload.get("step_id") or "step-unknown"),
                    step_kind=_normalize_step_kind(payload.get("step_kind")),
                    model_output=str(payload.get("model_output") or ""),
                    observations=[
                        str(item) for item in (payload.get("observations") or [])
                    ],
                    error=None
                    if payload.get("error") is None
                    else str(payload.get("error")),
                    usage=RunUsage(
                        input_tokens=_to_int(usage.get("input_tokens", 0)),
                        output_tokens=_to_int(usage.get("output_tokens", 0)),
                        reasoning_tokens=_to_int(usage.get("reasoning_tokens", 0)),
                    ),
                    duration_ms=_to_int(payload.get("duration_ms", 0)),
                    has_delta=bool(payload.get("has_delta")),
                ),
            )

    def _append_event(self, run_id: str, envelope, *, terminal: bool = False) -> None:
        if self.event_store is None:
            return
        self.event_store.append(run_id, envelope, terminal=terminal)

    def _create_cancellation_event(self, run_id: str):
        if self.cancellation_registry is None:
            return threading.Event()
        existing = self.cancellation_registry.get(run_id)
        if existing is not None:
            return existing
        return self.cancellation_registry.create(run_id)

    def _cancel_run_event(self, run_id: str) -> bool:
        if self.cancellation_registry is None:
            return False
        return self.cancellation_registry.cancel(run_id)

    def _clear_cancellation_event(self, run_id: str) -> None:
        if self.cancellation_registry is None:
            return
        self.cancellation_registry.clear(run_id)

    def _next_sequence(self, run_id: str) -> int:
        if self.event_store is None:
            return 1
        existing = self.event_store.list_after(run_id, 0)
        return len(existing) + 1

    def _last_sequence(self, run_id: str) -> int:
        if self.event_store is None:
            return 0
        existing = self.event_store.list_after(run_id, 0)
        if not existing:
            return 0
        return _to_int(existing[-1].data.get("sequence", 0))

    def _get_workspace_or_404(self, workspace_id: str):
        try:
            return self.workspaces.get_by_id(workspace_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found",
            ) from exc

    def _get_session_or_404(self, session_id: str):
        try:
            return self.sessions.get_by_id(session_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            ) from exc


def execute_runtime_run(
    config: RuntimeWorkerConfig,
    run_id: str,
    user_input: str,
    auth_context: dict[str, Any],
    metadata: RunMetadata,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    service = CodeAgentRuntimeService(config)
    return service.process(
        run_id=run_id,
        user_input=user_input,
        auth_context=auth_context,
        metadata=metadata,
        event_callback=event_callback,
    )


def serialize_run(run) -> dict[str, Any]:
    return {
        "id": run.id,
        "sessionId": run.session_id,
        "workspaceId": run.workspace_id,
        "connectionId": run.connection_id,
        "modelId": run.model_id,
        "status": run.status,
        "input": run.input_text,
        "finalAnswer": run.final_answer,
        "errorCode": run.error_code,
        "errorMessage": run.error_message,
        "createdAt": run.created_at,
        "startedAt": run.started_at,
        "completedAt": run.completed_at,
        "updatedAt": run.updated_at,
    }


def _normalize_required_string(value: Any, *, detail: str) -> str:
    text = str(value or "").strip()
    if text == "":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    return text


def _normalize_step_kind(value: object) -> Literal["planning", "action"]:
    text = str(value or "action").strip().lower()
    return "planning" if text == "planning" else "action"


def _to_int(value: object) -> int:
    try:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            return int(value.strip() or "0")
        return 0
    except (TypeError, ValueError):
        return 0
