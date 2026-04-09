from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import logging
import threading
from typing import Any, Callable, cast

from fastapi import BackgroundTasks, HTTPException, status

from nsbot_sidecar.infrastructure.attachment_store import AttachmentStore
from nsbot_sidecar.domain.run_cancellation import RunCancellationRegistry
from nsbot_sidecar.domain.run_event_store import RunEventStore
from nsbot_sidecar.domain.run_events import (
    completed_event,
    delta_event,
    failed_event,
    replay_ready_event,
    status_event,
    timeline_entry_event,
)
from nsbot_sidecar.infrastructure.local_paths import nsbot_home
from nsbot_sidecar.providers.provider_catalog import list_providers
from nsbot_sidecar.api.redaction import redact_text
from nsbot_sidecar.infrastructure.repositories import (
    AttachmentsRepository,
    DraftAttachmentsRepository,
    ProviderConnectionsRepository,
    RunsRepository,
    SessionsRepository,
    TimelineEntriesRepository,
    WorkspacesRepository,
    create_id,
    now_iso_timestamp,
)
from nsbot_sidecar.domain.agent_memory_projection import (
    project_final_answer_to_timeline_entry,
    project_system_notice_to_timeline_entry,
)
from nsbot_sidecar.runtime.runtime_service import (
    CodeAgentRuntimeService,
    RuntimeCancelledError,
    RunMetadata,
    RuntimeProcessError,
    RuntimeWorkerConfig,
)
from nsbot_sidecar.runtime.session_manager import SessionManager
from nsbot_sidecar.infrastructure.secret_store import LocalSecretStore
from nsbot_sidecar.application.session_service import SessionService
from nsbot_sidecar.infrastructure.storage import transaction
from nsbot_sidecar.application.timeline_service import serialize_timeline_entry, serialize_session_summary


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
    attachments: AttachmentsRepository
    draft_attachments: DraftAttachmentsRepository
    runs: RunsRepository
    timeline_entries: TimelineEntriesRepository
    session_service: SessionService
    attachment_store: AttachmentStore
    secret_store: LocalSecretStore
    event_store: RunEventStore | None = None
    cancellation_registry: RunCancellationRegistry | None = None
    ns_bot_home: str | None = None
    runtime_executor: RuntimeExecutor | None = None
    run_launcher: RunLauncher | None = None
    fd_executable: str | None = None
    rg_executable: str | None = None

    def create_run(
        self, payload: dict[str, Any], background_tasks: BackgroundTasks | None = None
    ) -> dict[str, Any]:
        session_id = _normalize_optional_string(
            payload.get("sessionId", payload.get("session_id"))
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
        reasoning_effort = _normalize_optional_string(
            payload.get("reasoningEffort", payload.get("reasoning_effort"))
        )
        input_text = _normalize_required_string(
            payload.get("input", payload.get("inputText", payload.get("input_text"))),
            detail="Run input is required",
        )
        attachment_ids = _normalize_attachment_ids(
            payload.get("attachmentIds", payload.get("attachment_ids"))
        )
        draft_attachment_ids = _normalize_draft_attachment_ids(
            payload.get("draftAttachmentIds", payload.get("draft_attachment_ids"))
        )
        if attachment_ids and draft_attachment_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="attachmentIds and draftAttachmentIds cannot be used together",
            )

        workspace = self._get_workspace_or_404(workspace_id)
        created_session_id: str | None = None
        promoted_drafts: list[tuple[str, str]] = []

        if session_id is None:
            session = self.sessions.create(
                workspace_id=workspace.id,
                active_connection_id=connection_id,
                active_model_id=model_id,
            )
            session_id = session.id
            created_session_id = session.id
        else:
            session = self._get_session_or_404(session_id)
            if session.workspace_id != workspace.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Session does not belong to workspace",
                )

        if draft_attachment_ids:
            draft_attachments = self.draft_attachments.list_by_ids(draft_attachment_ids)
            if len(draft_attachments) != len(set(draft_attachment_ids)):
                self._cleanup_created_session(created_session_id, promoted_drafts)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="One or more draft attachments were not found",
                )
            if any(
                draft_attachment.workspace_id != workspace_id
                for draft_attachment in draft_attachments
            ):
                self._cleanup_created_session(created_session_id, promoted_drafts)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Draft attachment does not belong to this workspace",
                )

            try:
                for draft_attachment in draft_attachments:
                    attachment_id = create_id("att")
                    target_storage_path = self.attachment_store.relative_path(
                        attachment_id, draft_attachment.file_name
                    )
                    self.attachment_store.move_file(
                        draft_attachment.storage_path, target_storage_path
                    )
                    promoted_drafts.append(
                        (draft_attachment.storage_path, target_storage_path)
                    )
                    attachment = self.attachments.create(
                        attachment_id=attachment_id,
                        session_id=session_id,
                        workspace_id=workspace_id,
                        file_name=draft_attachment.file_name,
                        mime_type=draft_attachment.mime_type,
                        size_bytes=draft_attachment.size_bytes,
                        storage_path=target_storage_path,
                        status="uploaded",
                    )
                    attachment_ids.append(attachment.id)
                    self.draft_attachments.delete_by_id(draft_attachment.id)
            except Exception:
                self._cleanup_created_session(created_session_id, promoted_drafts)
                raise

        attachments = self.attachments.list_by_ids(attachment_ids)
        if len(attachments) != len(set(attachment_ids)):
            self._cleanup_created_session(created_session_id, promoted_drafts)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more attachments were not found",
            )
        for attachment in attachments:
            if (
                attachment.session_id != session_id
                or attachment.workspace_id != workspace_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Attachment does not belong to this session",
                )
            if attachment.status != "uploaded":
                self._cleanup_created_session(created_session_id, promoted_drafts)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Attachment is not available",
                )

        bundle = self.providers.get_bundle_by_id(connection_id)
        if bundle is None:
            self._cleanup_created_session(created_session_id, promoted_drafts)
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

        user_entry = self.timeline_entries.append(
            session_id=session_id,
            run_id=run.id,
            entry_kind="user_input",
            display_role="user",
            content_text=input_text,
            content_json=_json_dumps_or_none(
                {"attachmentIds": attachment_ids} if attachment_ids else None
            ),
            created_at=run.created_at,
        )
        self.session_service.apply_first_user_message_title(
            session_id,
            input_text,
            active_connection_id=connection_id,
            active_model_id=model_id,
        )
        self.session_service.timeline_service.refresh_session_summary(
            session_id,
            active_connection_id=connection_id,
            active_model_id=model_id,
        )

        for attachment in attachments:
            self.attachments.update_status(attachment.id, "consumed")
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
            timeline_entry_event(
                run_id=run.id,
                session_id=session_id,
                sequence=self._next_sequence(run.id),
                created_at=user_entry.created_at,
                entry=serialize_timeline_entry(user_entry),
            ),
        )
        try:
            if not bundle.connection.is_enabled:
                raise RunValidationError(
                    code="provider_disabled",
                    detail="Provider connection is disabled",
                )

            self._ensure_model_allowed(bundle, model_id)
            resolved_reasoning_effort = self._resolve_reasoning_effort(
                bundle, model_id, reasoning_effort
            )

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
                provider=bundle.connection.runtime_provider,
                base_url=bundle.connection.base_url,
                api_key=api_key,
                model=model_id,
                direct_reasoning_effort=resolved_reasoning_effort,
                fd_executable=self.fd_executable,
                rg_executable=self.rg_executable,
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
        current_entries = [
            serialize_timeline_entry(entry)
            for entry in self.timeline_entries.list_by_session_id(session_id)
        ]

        return {
            "run": serialize_run(current_run),
            "session": serialize_session_summary(current_session),
            "entries": current_entries,
            "result": {
                "deltas": [],
                "entries": [],
                "finalAnswer": None,
            },
        }

    def edit_message_and_run(
        self,
        *,
        session_id: str,
        timeline_entry_id: str,
        payload: dict[str, Any],
        background_tasks: BackgroundTasks | None = None,
    ) -> dict[str, Any]:
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
        reasoning_effort = _normalize_optional_string(
            payload.get("reasoningEffort", payload.get("reasoning_effort"))
        )
        next_content = _normalize_required_string(
            payload.get("content", payload.get("input", payload.get("inputText"))),
            detail="Message content is required",
        )

        session = self._get_session_or_404(session_id)
        if session.workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session does not belong to workspace",
            )

        message = self.timeline_entries.get_by_id(timeline_entry_id)
        if message.session_id != session_id:
            raise HTTPException(status_code=404, detail="Message not found")
        if message.entry_kind != "user_input":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only user input timeline entries can be edited",
            )

        has_active_run = any(
            run.status in {"queued", "running"}
            for run in self.runs.list_by_session_id(session_id)
        )
        if has_active_run:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot edit while a run is in progress",
            )

        bundle = self.providers.get_bundle_by_id(connection_id)
        if bundle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider connection not found",
            )
        if not bundle.connection.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provider connection is disabled",
            )
        self._ensure_model_allowed(bundle, model_id)
        self._resolve_reasoning_effort(bundle, model_id, reasoning_effort)
        secret_payload = self.secret_store.load_provider_secret(
            bundle.connection.secret_ref
        )
        api_key = secret_payload.api_key if secret_payload is not None else None
        if api_key is None or api_key.strip() == "":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provider connection is missing an API key",
            )

        suffix_messages = self.timeline_entries.list_by_session_id_from_sequence(
            session_id, message.sequence_no
        )
        affected_run_ids = sorted(
            {
                candidate.run_id
                for candidate in suffix_messages
                if candidate.run_id is not None and candidate.run_id != ""
            }
        )

        with transaction(self.sessions.connection):
            self.timeline_entries.delete_by_session_id_from_sequence(
                session_id, message.sequence_no
            )
            if affected_run_ids:
                placeholders = ",".join("?" for _ in affected_run_ids)
                self.sessions.connection.execute(
                    f"DELETE FROM runs WHERE id IN ({placeholders})",
                    tuple(affected_run_ids),
                )

            self.session_service.timeline_service.refresh_session_summary(session_id)

        if self.event_store is not None and affected_run_ids:
            self.event_store.clear_many(affected_run_ids)

        runtime_sessions = SessionManager(str(nsbot_home(self.ns_bot_home)))
        runtime_session = runtime_sessions.get_or_create(session_id)
        for affected_run_id in affected_run_ids:
            runtime_session.truncate_by_run_id(affected_run_id)
        runtime_sessions.save(runtime_session)

        return self.create_run(
            {
                "sessionId": session_id,
                "workspaceId": workspace_id,
                "connectionId": connection_id,
                "modelId": model_id,
                "reasoningEffort": reasoning_effort,
                "input": next_content,
            },
            background_tasks=background_tasks,
        )

    def _cleanup_created_session(
        self, session_id: str | None, promoted_drafts: list[tuple[str, str]]
    ) -> None:
        for _, target_storage_path in promoted_drafts:
            self.attachment_store.delete_file(target_storage_path)
        if session_id is None:
            return
        try:
            self.sessions.delete_by_id(session_id)
        except Exception:
            LOGGER.exception(
                "Failed to clean up session %s after run bootstrap failure", session_id
            )

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
        except Exception as exc:
            LOGGER.exception("Unexpected runtime failure for run_id=%s", run_id)
            message = str(exc).strip() or exc.__class__.__name__
            self._record_failed_run(
                run_id=run_id,
                session_id=session_id,
                connection_id=connection_id,
                model_id=model_id,
                error_code="runtime_error",
                error_message=f"Unexpected runtime error: {message}",
                started_at=started_at,
            )
            self._clear_cancellation_event(run_id)
            return

        final_answer = str(result.get("final_answer") or "").strip() or "Completed."
        if not emitted_live_events:
            self._emit_runtime_events(run_id, session_id, result, started_at)

        final_answer_entry = self.timeline_entries.append(
            created_at=started_at,
            **project_final_answer_to_timeline_entry(
                final_answer,
                run_id=run_id,
                session_id=session_id,
            ),
        )
        self.session_service.timeline_service.refresh_session_summary(
            session_id,
            active_connection_id=connection_id,
            active_model_id=model_id,
            trigger_title_generation=True,
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
            timeline_entry_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=completed_run.completed_at or completed_run.updated_at,
                entry=serialize_timeline_entry(final_answer_entry),
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
        parameters = signature.parameters
        has_var_keyword = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        has_var_positional = any(
            parameter.kind == inspect.Parameter.VAR_POSITIONAL
            for parameter in parameters.values()
        )
        supports_event_callback = (
            "event_callback" in parameters
            or has_var_keyword
            or has_var_positional
            or len(parameters) >= 6
        )
        supports_is_cancelled = (
            "is_cancelled" in parameters
            or has_var_keyword
            or has_var_positional
            or len(parameters) >= 7
        )
        base_args = [config, run_id, user_input, auth_context, metadata]

        if has_var_positional:
            if supports_event_callback:
                base_args.append(event_callback)
            if supports_is_cancelled:
                base_args.append(is_cancelled)
            return executor_any(*base_args), supports_event_callback

        kwargs: dict[str, Any] = {}
        if supports_event_callback and (
            "event_callback" in parameters or has_var_keyword
        ):
            kwargs["event_callback"] = event_callback
        if supports_is_cancelled and ("is_cancelled" in parameters or has_var_keyword):
            kwargs["is_cancelled"] = is_cancelled

        if kwargs:
            return executor_any(*base_args, **kwargs), supports_event_callback

        if supports_event_callback:
            call_args = list(base_args)
            call_args.append(event_callback)
            if supports_is_cancelled:
                call_args.append(is_cancelled)
            return executor_any(*call_args), True

        return executor_any(*base_args), False

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

    def list_run_timeline_payload(self, run_id: str) -> dict[str, Any]:
        self.runs.get_by_id(run_id)
        return {
            "entries": [
                serialize_timeline_entry(entry)
                for entry in self.timeline_entries.list_by_run_id(run_id)
            ]
        }

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

    def _resolve_reasoning_effort(
        self, bundle, model_id: str, requested_reasoning_effort: str | None
    ) -> str | None:
        allowed_values = self._allowed_reasoning_effort_values(bundle, model_id)
        if not allowed_values:
            if requested_reasoning_effort is not None:
                raise RunValidationError(
                    code="invalid_reasoning_effort",
                    detail="Selected model does not support reasoning effort",
                )
            return None

        if requested_reasoning_effort is None:
            return _default_reasoning_effort(allowed_values)

        if requested_reasoning_effort not in allowed_values:
            raise RunValidationError(
                code="invalid_reasoning_effort",
                detail="Reasoning effort is not supported for this model",
            )
        return requested_reasoning_effort

    def _allowed_reasoning_effort_values(self, bundle, model_id: str) -> list[str]:
        connection = bundle.connection
        if connection.kind == "custom":
            return []

        catalog_by_id = {provider["id"]: provider for provider in list_providers()}
        provider_id = connection.catalog_provider_id or ""
        catalog_entry = catalog_by_id.get(provider_id)
        if not catalog_entry:
            return []

        for model in catalog_entry.get("models", []):
            if str(model.get("id") or "") != model_id:
                continue
            values = model.get("reasoningEffortValues")
            if not isinstance(values, list):
                return []
            return [str(value) for value in values if str(value)]
        return []

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
        notice_entry = self.timeline_entries.append(
            created_at=failed_run.completed_at or failed_run.updated_at,
            **project_system_notice_to_timeline_entry(
                f"Run failed: {error_message}",
                run_id=run_id,
                session_id=session_id,
                notice_code="failed",
            ),
        )
        self.session_service.timeline_service.refresh_session_summary(session_id)
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
        self._append_event(
            run_id,
            status_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=notice_entry.created_at,
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
                created_at=notice_entry.created_at,
                error_code=error_code,
                error_message=error_message,
            ),
        )
        self._append_event(
            run_id,
            timeline_entry_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=notice_entry.created_at,
                entry=serialize_timeline_entry(notice_entry),
            ),
        )
        self._append_event(
            run_id,
            replay_ready_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=notice_entry.created_at,
                last_event_sequence=self._last_sequence(run_id),
            ),
            terminal=True,
        )
        return {
            "detail": error_message,
            "run": serialize_run(failed_run),
            "session": serialize_session_summary(current_session),
            "entries": [
                serialize_timeline_entry(entry)
                for entry in self.timeline_entries.list_by_session_id(session_id)
            ],
            "result": {
                "deltas": [],
                "entries": [],
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
        system_message = self.timeline_entries.append(
            created_at=cancelled_run.completed_at or cancelled_run.updated_at,
            **project_system_notice_to_timeline_entry(
                "Run cancelled",
                run_id=run_id,
                session_id=session_id,
                notice_code="cancelled",
            ),
        )
        self.session_service.timeline_service.refresh_session_summary(session_id)
        self._append_event(
            run_id,
            status_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=system_message.created_at,
                status="cancelled",
                message="Run cancelled",
            ),
        )
        self._append_event(
            run_id,
            timeline_entry_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=system_message.created_at,
                entry=serialize_timeline_entry(system_message),
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

        for entry in result.get("timeline_entries", []):
            persisted = self.timeline_entries.append(created_at=created_at, **entry)
            self._append_runtime_timeline_event(
                run_id, session_id, created_at, persisted
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

        if event_type == "timeline_entry":
            persisted = self.timeline_entries.append(created_at=created_at, **payload)
            self._append_runtime_timeline_event(
                run_id, session_id, created_at, persisted
            )

    def _append_runtime_timeline_event(
        self, run_id: str, session_id: str, created_at: str, payload
    ) -> None:
        self._append_event(
            run_id,
            timeline_entry_event(
                run_id=run_id,
                session_id=session_id,
                sequence=self._next_sequence(run_id),
                created_at=created_at,
                entry=serialize_timeline_entry(payload),
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
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    service = CodeAgentRuntimeService(config)
    return service.process(
        run_id=run_id,
        user_input=user_input,
        auth_context=auth_context,
        metadata=metadata,
        event_callback=event_callback,
        is_cancelled=is_cancelled,
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=redact_text(detail),
        )
    return text


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_attachment_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="attachmentIds must be a list of strings",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _normalize_optional_string(item)
        if text is None or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _normalize_draft_attachment_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="draftAttachmentIds must be a list of strings",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _normalize_optional_string(item)
        if text is None or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _normalize_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return _to_int(value)


def _json_dumps_or_none(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False)


def _default_reasoning_effort(values: list[str]) -> str | None:
    if not values:
        return None
    for candidate in ("medium", "low", "high", "none", "minimal", "xhigh"):
        if candidate in values:
            return candidate
    return values[0]


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
