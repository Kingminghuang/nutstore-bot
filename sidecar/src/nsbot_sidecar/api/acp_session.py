from __future__ import annotations

import asyncio
import base64
from concurrent.futures import Future
import json
import sys
from pathlib import Path
import threading
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from nsbot_sidecar.api.discovery import nsbot_home
from nsbot_sidecar.infrastructure.repositories import create_id
from nsbot_sidecar.infrastructure.storage import transaction
from nsbot_sidecar.runtime.engine import create_runtime_engine
from nsbot_sidecar.runtime.runtime_service import (
    RunMetadata,
    RuntimeCancelledError,
    RuntimeWorkerConfig,
)


@dataclass
class _ClientRequestWaiter:
    future: Future
    session_id: str


class JsonRpcTransport(Protocol):
    async def accept(self) -> None: ...

    async def receive_json(self) -> dict[str, Any]: ...

    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


class StdioJsonRpcTransport:
    async def accept(self) -> None:
        return

    async def receive_json(self) -> dict[str, Any]:
        line = await asyncio.to_thread(sys.stdin.readline)
        if line == "":
            raise EOFError("stdin closed")
        try:
            payload = json.loads(line)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    async def send_json(self, payload: dict[str, Any]) -> None:
        print(json.dumps(payload, ensure_ascii=False), flush=True)

    async def close(self) -> None:
        return


class AcpJsonRpcSession:
    def __init__(self, transport: JsonRpcTransport, app_state: Any):
        self.transport = transport
        self.state = app_state
        self.loop: asyncio.AbstractEventLoop | None = None
        self._send_lock = asyncio.Lock()
        self._pending_lock = threading.Lock()

        self._next_rpc_id = 1000
        self._pending_client_calls: dict[int, _ClientRequestWaiter] = {}
        self._prompt_tasks: dict[str, asyncio.Task[Any]] = {}
        self._session_cancel_events: dict[str, threading.Event] = {}
        self._session_thought_levels: dict[str, str] = {}

        self._client_capabilities: dict[str, Any] = {
            "fs": {"readTextFile": False, "writeTextFile": False},
            "terminal": False,
        }

    async def run(self) -> None:
        await self.transport.accept()
        self.loop = asyncio.get_running_loop()
        try:
            while True:
                payload = await self.transport.receive_json()
                await self._dispatch(payload)
        except EOFError:
            self._cancel_all_sessions()
        except RuntimeError as exc:
            # Starlette may raise RuntimeError on abrupt client disconnect in tests/runtime.
            if "WebSocket is not connected" in str(exc):
                self._cancel_all_sessions()
                return
            raise

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return

        if "method" in payload:
            if "id" in payload:
                await self._handle_request(payload)
            else:
                await self._handle_notification(payload)
            return

        if "id" in payload:
            self._handle_client_response(payload)

    async def _handle_request(self, payload: dict[str, Any]) -> None:
        method = str(payload.get("method") or "")
        req_id = payload.get("id")
        params = payload.get("params") or {}

        try:
            if method == "initialize":
                await self._send_result(req_id, self._handle_initialize(params))
                return
            if method == "authenticate":
                await self._send_result(req_id, {"_meta": {}})
                return
            if method == "disconnect":
                await self._send_result(req_id, {})
                await self.transport.close()
                return
            if method == "session/new":
                await self._send_result(req_id, self._handle_session_new(params))
                return
            if method == "session/list":
                await self._send_result(req_id, self._handle_session_list(params))
                return
            if method == "workspace/list":
                await self._send_result(
                    req_id,
                    self.state.session_service.list_workspaces_payload(),
                )
                return
            if method == "workspace/create":
                await self._send_result(
                    req_id,
                    self.state.session_service.create_workspace(params),
                )
                return
            if method == "workspace/update":
                workspace_id = str(
                    params.get("workspaceId") or params.get("workspace_id") or ""
                )
                if not workspace_id:
                    await self._send_error(req_id, -32000, "workspaceId is required")
                    return
                await self._send_result(
                    req_id,
                    self.state.session_service.update_workspace(workspace_id, params),
                )
                return
            if method == "workspace/delete":
                workspace_id = str(
                    params.get("workspaceId") or params.get("workspace_id") or ""
                )
                if not workspace_id:
                    await self._send_error(req_id, -32000, "workspaceId is required")
                    return
                self.state.session_service.delete_workspace(workspace_id)
                await self._send_result(req_id, {})
                return
            if method == "workspace/sessions/list":
                workspace_id = str(
                    params.get("workspaceId") or params.get("workspace_id") or ""
                )
                if not workspace_id:
                    await self._send_error(req_id, -32000, "workspaceId is required")
                    return
                await self._send_result(
                    req_id,
                    self.state.session_service.list_sessions_payload(workspace_id),
                )
                return
            if method == "workspace/sessions/create":
                workspace_id = str(
                    params.get("workspaceId") or params.get("workspace_id") or ""
                )
                if not workspace_id:
                    await self._send_error(req_id, -32000, "workspaceId is required")
                    return
                await self._send_result(
                    req_id,
                    self.state.session_service.create_session(workspace_id, params),
                )
                return
            if method == "workspace/sidecar_index/status":
                workspace_id = str(
                    params.get("workspaceId") or params.get("workspace_id") or ""
                )
                if not workspace_id:
                    await self._send_error(req_id, -32000, "workspaceId is required")
                    return
                await self._send_result(
                    req_id,
                    self.state.session_service.workspace_sidecar_index_status_payload(
                        workspace_id
                    ),
                )
                return
            if method == "provider/catalog":
                await self._send_result(
                    req_id,
                    self.state.provider_service.catalog_payload(),
                )
                return
            if method == "provider/list":
                await self._send_result(
                    req_id,
                    self.state.provider_service.list_connections_payload(),
                )
                return
            if method == "provider/model_options":
                await self._send_result(
                    req_id,
                    self.state.provider_service.model_options_payload(),
                )
                return
            if method == "provider/create":
                await self._send_result(
                    req_id,
                    self.state.provider_service.create_provider(params),
                )
                return
            if method == "provider/update":
                provider_id = str(
                    params.get("providerId") or params.get("provider_id") or ""
                )
                if not provider_id:
                    await self._send_error(req_id, -32000, "providerId is required")
                    return
                await self._send_result(
                    req_id,
                    self.state.provider_service.update_provider(provider_id, params),
                )
                return
            if method == "provider/delete":
                provider_id = str(
                    params.get("providerId") or params.get("provider_id") or ""
                )
                if not provider_id:
                    await self._send_error(req_id, -32000, "providerId is required")
                    return
                self.state.provider_service.delete_provider(provider_id)
                await self._send_result(req_id, {})
                return
            if method == "provider/validate":
                provider_id = str(
                    params.get("providerId") or params.get("provider_id") or ""
                )
                if not provider_id:
                    await self._send_error(req_id, -32000, "providerId is required")
                    return
                await self._send_result(
                    req_id,
                    self.state.provider_service.validate_provider(provider_id, params),
                )
                return
            if method == "session/load":
                await self._send_result(req_id, await self._handle_session_load(params))
                return
            if method == "session/resume":
                await self._send_result(req_id, self._handle_session_resume(params))
                return
            if method == "session/update_meta":
                session_id = str(
                    params.get("sessionId") or params.get("session_id") or ""
                )
                if not session_id:
                    await self._send_error(req_id, -32000, "sessionId is required")
                    return
                await self._send_result(
                    req_id,
                    self.state.session_service.update_session(session_id, params),
                )
                return
            if method == "session/delete":
                session_id = str(
                    params.get("sessionId") or params.get("session_id") or ""
                )
                if not session_id:
                    await self._send_error(req_id, -32000, "sessionId is required")
                    return
                self.state.session_service.delete_session(session_id)
                await self._send_result(req_id, {})
                return
            if method == "timeline/list":
                await self._send_result(req_id, self._handle_timeline_list(params))
                return
            if method == "draft_attachment/list":
                workspace_id = str(
                    params.get("workspaceId") or params.get("workspace_id") or ""
                )
                if not workspace_id:
                    await self._send_error(req_id, -32000, "workspaceId is required")
                    return
                await self._send_result(
                    req_id,
                    self.state.session_service.list_draft_attachments_payload(
                        workspace_id
                    ),
                )
                return
            if method == "draft_attachment/delete":
                workspace_id = str(
                    params.get("workspaceId") or params.get("workspace_id") or ""
                )
                draft_attachment_id = str(
                    params.get("draftAttachmentId")
                    or params.get("draft_attachment_id")
                    or ""
                )
                if not workspace_id or not draft_attachment_id:
                    await self._send_error(
                        req_id, -32000, "workspaceId and draftAttachmentId are required"
                    )
                    return
                self.state.session_service.delete_draft_attachment(
                    workspace_id, draft_attachment_id
                )
                await self._send_result(req_id, {})
                return
            if method == "draft_attachment/create":
                workspace_id = str(
                    params.get("workspaceId") or params.get("workspace_id") or ""
                )
                file_name = str(
                    params.get("fileName") or params.get("file_name") or "attachment"
                )
                mime_type = str(
                    params.get("mimeType")
                    or params.get("mime_type")
                    or "application/octet-stream"
                )
                payload_b64 = str(params.get("payloadBase64") or "")
                if not workspace_id or not payload_b64:
                    await self._send_error(
                        req_id, -32000, "workspaceId and payloadBase64 are required"
                    )
                    return
                try:
                    payload_bytes = base64.b64decode(payload_b64)
                except Exception:  # noqa: BLE001
                    await self._send_error(req_id, -32000, "payloadBase64 is invalid")
                    return
                result = self.state.session_service.create_draft_attachment(
                    workspace_id=workspace_id,
                    file_name=file_name,
                    mime_type=mime_type,
                    payload=payload_bytes,
                )
                await self._send_result(req_id, result)
                return
            if method == "attachment/list":
                session_id = str(
                    params.get("sessionId") or params.get("session_id") or ""
                )
                if not session_id:
                    await self._send_error(req_id, -32000, "sessionId is required")
                    return
                await self._send_result(
                    req_id,
                    self.state.session_service.list_attachments_payload(session_id),
                )
                return
            if method == "attachment/delete":
                session_id = str(
                    params.get("sessionId") or params.get("session_id") or ""
                )
                attachment_id = str(
                    params.get("attachmentId") or params.get("attachment_id") or ""
                )
                if not session_id or not attachment_id:
                    await self._send_error(
                        req_id, -32000, "sessionId and attachmentId are required"
                    )
                    return
                self.state.session_service.delete_attachment(session_id, attachment_id)
                await self._send_result(req_id, {})
                return
            if method == "attachment/create":
                session_id = str(
                    params.get("sessionId") or params.get("session_id") or ""
                )
                file_name = str(
                    params.get("fileName") or params.get("file_name") or "attachment"
                )
                mime_type = str(
                    params.get("mimeType")
                    or params.get("mime_type")
                    or "application/octet-stream"
                )
                payload_b64 = str(params.get("payloadBase64") or "")
                if not session_id or not payload_b64:
                    await self._send_error(
                        req_id, -32000, "sessionId and payloadBase64 are required"
                    )
                    return
                try:
                    payload_bytes = base64.b64decode(payload_b64)
                except Exception:  # noqa: BLE001
                    await self._send_error(req_id, -32000, "payloadBase64 is invalid")
                    return
                result = self.state.session_service.create_attachment(
                    session_id=session_id,
                    file_name=file_name,
                    mime_type=mime_type,
                    payload=payload_bytes,
                )
                await self._send_result(req_id, result)
                return
            if method == "session/set_config_option":
                await self._send_result(req_id, self._handle_set_config_option(params))
                return
            if method == "session/cancel":
                session_id = str(params.get("sessionId") or "")
                self._cancel_session(session_id)
                await self._send_result(req_id, {"cancelled": True})
                return
            if method == "session/prompt":
                session_id = str(params.get("sessionId") or "")
                if not session_id:
                    await self._send_error(req_id, -32000, "sessionId is required")
                    return
                existing = self._prompt_tasks.get(session_id)
                if existing is not None and not existing.done():
                    await self._send_error(req_id, -32000, "session already running")
                    return
                task = asyncio.create_task(self._handle_prompt_request(req_id, params))
                self._prompt_tasks[session_id] = task
                return
            if method == "session/edit_and_prompt":
                session_id = str(params.get("sessionId") or "")
                if not session_id:
                    await self._send_error(req_id, -32000, "sessionId is required")
                    return
                existing = self._prompt_tasks.get(session_id)
                if existing is not None and not existing.done():
                    await self._send_error(req_id, -32000, "session already running")
                    return
                task = asyncio.create_task(
                    self._handle_edit_and_prompt_request(req_id, params)
                )
                self._prompt_tasks[session_id] = task
                return

            await self._send_error(req_id, -32601, f"Method not found: {method}")
        except Exception as exc:  # noqa: BLE001
            await self._send_error(req_id, -32000, str(exc))

    async def _handle_notification(self, payload: dict[str, Any]) -> None:
        method = str(payload.get("method") or "")
        params = payload.get("params") or {}
        if method == "session/cancel":
            session_id = str(params.get("sessionId") or "")
            self._cancel_session(session_id)

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        capabilities = params.get("clientCapabilities")
        if isinstance(capabilities, dict):
            self._client_capabilities = capabilities

        return {
            "protocolVersion": 1,
            "agentCapabilities": {
                "loadSession": True,
                "promptCapabilities": {
                    "image": True,
                    "audio": True,
                    "embeddedContext": True,
                },
                "sessionCapabilities": {
                    "list": {},
                    "resume": {},
                },
            },
            "agentInfo": {
                "name": "nutstore-sidecar",
                "title": "Nutstore Sidecar",
                "version": "0.1.0",
            },
            "authMethods": [
                {
                    "id": "api-key",
                    "name": "API Key",
                    "type": "env_var",
                    "vars": [{"name": "OPENAI_API_KEY"}],
                }
            ],
        }

    def _ensure_session_exists(self, session_id: str):
        return self.state.repositories.sessions.get_by_id(session_id)

    def _find_or_create_workspace(self, cwd: str):
        normalized = str(Path(cwd).expanduser().resolve())
        for workspace in self.state.repositories.workspaces.list():
            if workspace.real_path == normalized:
                return workspace
        name = Path(normalized).name or "workspace"
        return self.state.repositories.workspaces.create(
            name=name,
            path_label=normalized,
            real_path=normalized,
        )

    def _default_selection(self) -> dict[str, str]:
        payload = self.state.provider_service.model_options_payload()
        selection = payload.get("defaultSelection")
        if not isinstance(selection, dict):
            raise RuntimeError("No default provider/model available")

        connection_id = str(selection.get("connectionId") or "")
        model_id = str(selection.get("modelId") or "")
        if not connection_id or not model_id:
            raise RuntimeError("No default provider/model available")

        return {"connectionId": connection_id, "modelId": model_id}

    def _config_options_for_session(self, session_id: str) -> list[dict[str, Any]]:
        session = self.state.repositories.sessions.get_by_id(session_id)
        model_payload = self.state.provider_service.model_options_payload()
        groups = model_payload.get("groups")

        available_models: list[dict[str, Any]] = []
        reasoning_values: list[str] = []
        if isinstance(groups, list):
            for group in groups:
                if str(group.get("connectionId") or "") != str(
                    session.active_connection_id or ""
                ):
                    continue
                models = group.get("models")
                if not isinstance(models, list):
                    continue
                for model in models:
                    model_id = str(model.get("modelId") or "")
                    if not model_id:
                        continue
                    available_models.append(
                        {
                            "value": model_id,
                            "name": str(model.get("label") or model_id),
                            "description": str(model.get("description") or "") or None,
                        }
                    )
                    if model_id == str(session.active_model_id or ""):
                        values = model.get("reasoningEffortValues")
                        if isinstance(values, list):
                            reasoning_values = [str(v) for v in values if str(v)]

        thought_level = self._session_thought_levels.get(session_id)
        if not thought_level:
            thought_level = (
                "medium"
                if "medium" in reasoning_values
                else (reasoning_values[0] if reasoning_values else "medium")
            )

        options: list[dict[str, Any]] = [
            {
                "id": "mode",
                "name": "Session Mode",
                "category": "mode",
                "type": "select",
                "currentValue": "ask",
                "options": [
                    {
                        "value": "ask",
                        "name": "Ask",
                        "description": "Only ask permission for controlled actions",
                    }
                ],
            }
        ]
        if available_models:
            options.append(
                {
                    "id": "model",
                    "name": "Model",
                    "category": "model",
                    "type": "select",
                    "currentValue": str(
                        session.active_model_id or available_models[0]["value"]
                    ),
                    "options": available_models,
                }
            )
        if reasoning_values:
            options.append(
                {
                    "id": "thought_level",
                    "name": "Thought Level",
                    "category": "thought_level",
                    "type": "select",
                    "currentValue": thought_level,
                    "options": [
                        {"value": value, "name": value, "description": None}
                        for value in reasoning_values
                    ],
                }
            )
        return options

    def _handle_session_new(self, params: dict[str, Any]) -> dict[str, Any]:
        cwd = str(params.get("cwd") or params.get("workspacePath") or "").strip()
        if not cwd:
            raise RuntimeError("cwd is required")

        workspace = self._find_or_create_workspace(cwd)
        selection = self._default_selection()
        session = self.state.repositories.sessions.create(
            workspace_id=workspace.id,
            active_connection_id=selection["connectionId"],
            active_model_id=selection["modelId"],
        )

        return {
            "sessionId": session.id,
            "configOptions": self._config_options_for_session(session.id),
        }

    def _handle_session_list(self, params: dict[str, Any]) -> dict[str, Any]:
        cwd = str(params.get("cwd") or "").strip()
        normalized = str(Path(cwd).expanduser().resolve()) if cwd else ""

        sessions: list[dict[str, Any]] = []
        workspaces = {ws.id: ws for ws in self.state.repositories.workspaces.list()}
        for workspace in workspaces.values():
            if normalized and str(workspace.real_path) != normalized:
                continue
            for session in self.state.repositories.sessions.list_by_workspace_id(
                workspace.id
            ):
                sessions.append(
                    {
                        "sessionId": session.id,
                        "cwd": workspace.real_path,
                        "title": session.title,
                        "updatedAt": session.updated_at,
                    }
                )

        sessions.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return {"sessions": sessions}

    async def _replay_session_history(self, session_id: str) -> None:
        events = self.state.repositories.acp_event_log.list_by_session_id(session_id)
        for event in events:
            try:
                payload = json.loads(event.event_json)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            method = str(payload.get("method") or "")
            params = payload.get("params")
            if method == "session/update" and isinstance(params, dict):
                await self._send_notification(method, params)

    def _handle_timeline_list(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("sessionId") or params.get("session_id") or "")
        if not session_id:
            raise RuntimeError("sessionId is required")
        self._ensure_session_exists(session_id)

        limit_value = params.get("limit")
        before_sequence_value = params.get("beforeSequence")
        if before_sequence_value is None:
            before_sequence_value = params.get("before_sequence")
        limit = (
            int(limit_value)
            if isinstance(limit_value, (int, float, str)) and str(limit_value).strip()
            else 100
        )
        if limit <= 0:
            limit = 100
        before_sequence = (
            int(before_sequence_value)
            if isinstance(before_sequence_value, (int, float, str))
            and str(before_sequence_value).strip()
            else None
        )

        events, has_more, next_before_sequence = (
            self.state.repositories.acp_event_log.list_by_session_id_page(
                session_id,
                limit=limit,
                before_sequence=before_sequence,
            )
        )
        return {
            "events": [self._serialize_event_log(event) for event in events],
            "pagination": {
                "hasMore": has_more,
                "nextBeforeSequence": next_before_sequence,
            },
        }

    async def _handle_session_load(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("sessionId") or "")
        if not session_id:
            raise RuntimeError("sessionId is required")
        self._ensure_session_exists(session_id)
        return {"configOptions": self._config_options_for_session(session_id)}

    def _handle_session_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("sessionId") or "")
        if not session_id:
            raise RuntimeError("sessionId is required")
        self._ensure_session_exists(session_id)
        return {"configOptions": self._config_options_for_session(session_id)}

    def _handle_set_config_option(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("sessionId") or "")
        config_id = str(params.get("configId") or "")
        value = str(params.get("value") or "")
        if not session_id or not config_id:
            raise RuntimeError("sessionId and configId are required")

        session = self._ensure_session_exists(session_id)

        if config_id == "mode":
            if value != "ask":
                raise RuntimeError("mode only supports ask")
        elif config_id == "model":
            self.state.repositories.sessions.touch(session.id, active_model_id=value)
        elif config_id == "thought_level":
            self._session_thought_levels[session_id] = value
        else:
            raise RuntimeError(f"Unsupported config option: {config_id}")

        return {"configOptions": self._config_options_for_session(session_id)}

    def _client_supports_read_text_file(self) -> bool:
        fs_cap = self._client_capabilities.get("fs")
        return isinstance(fs_cap, dict) and bool(fs_cap.get("readTextFile"))

    async def _extract_prompt_text(
        self, session_id: str, blocks: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        parts: list[str] = []
        normalized_blocks: list[dict[str, Any]] = []

        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")

            if block_type == "text":
                text = str(block.get("text") or "")
                if text.strip():
                    parts.append(text)
                normalized_blocks.append({"type": "text", "text": text})
                continue

            if block_type == "image":
                mime = str(block.get("mimeType") or block.get("mime_type") or "image/*")
                parts.append(f"[Image content: {mime}]")
                normalized_blocks.append(block)
                continue

            if block_type == "audio":
                mime = str(block.get("mimeType") or block.get("mime_type") or "audio/*")
                parts.append(f"[Audio content: {mime}]")
                normalized_blocks.append(block)
                continue

            if block_type == "resource":
                resource = block.get("resource")
                resource_text = self._resource_text_from_embedded(resource)
                if resource_text:
                    parts.append(resource_text)
                normalized_blocks.append(block)
                continue

            if block_type == "resource_link":
                uri = str(block.get("uri") or "").strip()
                if (
                    uri
                    and self._client_supports_read_text_file()
                    and self._looks_like_file_uri(uri)
                ):
                    try:
                        content = await self._request_client_read_text_file(
                            session_id, uri
                        )
                        if content.strip():
                            parts.append(content)
                        normalized_blocks.append(
                            {
                                "type": "resource",
                                "resource": {
                                    "uri": uri,
                                    "mimeType": "text/plain",
                                    "text": content,
                                },
                            }
                        )
                        continue
                    except Exception:
                        # Fallback to URI-only projection.
                        pass
                if uri:
                    parts.append(uri)
                normalized_blocks.append(block)
                continue

            normalized_blocks.append(block)

        return "\n".join(
            [part for part in parts if part.strip()]
        ).strip(), normalized_blocks

    def _resource_text_from_embedded(self, resource: Any) -> str:
        if not isinstance(resource, dict):
            return ""

        text = resource.get("text")
        if isinstance(text, str) and text.strip():
            return text

        data = resource.get("data")
        if isinstance(data, str) and data.strip():
            return data

        uri = resource.get("uri")
        if isinstance(uri, str) and uri.strip():
            return uri

        return ""

    def _looks_like_file_uri(self, uri: str) -> bool:
        if uri.startswith("file://"):
            return True
        parsed = urlparse(uri)
        return parsed.scheme == "" and (uri.startswith("/") or uri.startswith("~"))

    def _uri_to_fs_path(self, uri: str) -> str:
        if uri.startswith("file://"):
            parsed = urlparse(uri)
            path = unquote(parsed.path)
            if parsed.netloc and parsed.netloc not in {"", "localhost"}:
                path = f"//{parsed.netloc}{path}"
            return str(Path(path).expanduser())
        return str(Path(uri).expanduser())

    async def _handle_edit_and_prompt_request(
        self, req_id: Any, params: dict[str, Any]
    ) -> None:
        session_id = str(params.get("sessionId") or "")
        event_id = str(params.get("eventId") or params.get("event_id") or "")
        delegated = False
        try:
            if not session_id:
                raise RuntimeError("sessionId is required")
            if not event_id:
                raise RuntimeError("eventId is required")

            session = self._ensure_session_exists(session_id)
            event_anchor = self._resolve_edit_anchor_from_event(session_id, event_id)
            if event_anchor is None:
                raise RuntimeError("Event not found")
            prompt_blocks = params.get("prompt")
            if not isinstance(prompt_blocks, list):
                raise RuntimeError("prompt must be content block array")
            user_text, normalized_blocks = await self._extract_prompt_text(
                session_id, prompt_blocks
            )
            if not user_text:
                raise RuntimeError("prompt text is empty")

            with transaction(self.state.database):
                self.state.repositories.acp_event_log.delete_by_session_id_from_sequence(
                    session_id, event_anchor["sequenceNo"]
                )
                self.state.session_service.timeline_service.refresh_session_summary(
                    session_id,
                    active_connection_id=session.active_connection_id,
                    active_model_id=session.active_model_id,
                )

            delegated = True
            await self._handle_prompt_request(
                req_id,
                {
                    **params,
                    "sessionId": session_id,
                    "prompt": prompt_blocks,
                    "_preparsedPromptText": user_text,
                    "_preparsedPromptBlocks": normalized_blocks,
                },
            )
        except Exception as exc:  # noqa: BLE001
            if not delegated:
                self._prompt_tasks.pop(session_id, None)
            await self._send_error(req_id, -32000, str(exc))

    def _resolve_edit_anchor_from_event(
        self, session_id: str, event_id: str
    ) -> dict[str, Any] | None:
        if not event_id:
            return None
        try:
            event = self.state.repositories.acp_event_log.get_by_id(event_id)
        except ValueError as exc:
            raise RuntimeError("Event not found") from exc
        if event.session_id != session_id:
            raise RuntimeError("Event not found")
        try:
            payload = json.loads(event.event_json)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Event payload is invalid") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Event payload is invalid")
        params = payload.get("params")
        if not isinstance(params, dict):
            raise RuntimeError("Event payload is invalid")
        update = params.get("update")
        if not isinstance(update, dict):
            raise RuntimeError("Event is not editable")
        if str(update.get("sessionUpdate") or "") != "user_message_chunk":
            raise RuntimeError("Only user input events can be edited")
        content = update.get("content")
        if not isinstance(content, dict):
            raise RuntimeError("Event payload is invalid")
        if str(content.get("type") or "") != "text":
            raise RuntimeError("Only text user message events can be edited")
        message_text = str(content.get("text") or "")
        return {"sequenceNo": event.sequence_no, "messageText": message_text}

    async def _handle_prompt_request(self, req_id: Any, params: dict[str, Any]) -> None:
        session_id = str(params.get("sessionId") or "")
        stop_reason = "end_turn"

        try:
            if not session_id:
                raise RuntimeError("sessionId is required")

            session = self._ensure_session_exists(session_id)
            workspace = self.state.repositories.workspaces.get_by_id(
                session.workspace_id
            )

            prompt_blocks = params.get("prompt")
            user_text = params.get("_preparsedPromptText")
            normalized_blocks = params.get("_preparsedPromptBlocks")
            if (
                not isinstance(user_text, str)
                or not user_text.strip()
                or not isinstance(normalized_blocks, list)
            ):
                if not isinstance(prompt_blocks, list):
                    raise RuntimeError("prompt must be content block array")
                user_text, normalized_blocks = await self._extract_prompt_text(
                    session_id, prompt_blocks
                )
                if not user_text:
                    raise RuntimeError("prompt text is empty")

            auto_allow = True
            meta = params.get("_meta")
            if isinstance(meta, dict) and isinstance(meta.get("autoAllow"), bool):
                auto_allow = bool(meta.get("autoAllow"))

            cancel_event = self._session_cancel_events.setdefault(
                session_id, threading.Event()
            )
            cancel_event.clear()

            self.state.session_service.timeline_service.refresh_session_summary(
                session_id,
                active_connection_id=session.active_connection_id,
                active_model_id=session.active_model_id,
            )

            await self._emit_session_update(
                session_id,
                {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": user_text},
                },
            )

            bundle = self.state.repositories.providers.get_bundle_by_id_or_raise(
                str(session.active_connection_id or "")
            )
            secret_payload = self.state.secret_store.load_provider_secret(
                bundle.connection.secret_ref
            )
            api_key = secret_payload.api_key if secret_payload is not None else None
            if not api_key:
                raise RuntimeError("Provider connection is missing an API key")

            thought_level = self._session_thought_levels.get(session_id)
            if not thought_level and isinstance(meta, dict):
                selected = meta.get("selectedReasoningEffort")
                if isinstance(selected, str) and selected.strip():
                    thought_level = selected.strip()

            model_id = str(session.active_model_id or "")
            config = RuntimeWorkerConfig(
                model_id=model_id,
                provider=bundle.connection.runtime_provider,
                base_url=bundle.connection.base_url,
                api_key=api_key,
                model=model_id,
                direct_reasoning_effort=thought_level,
                ns_bot_home=str(nsbot_home(self.state.api_server_config.ns_bot_home)),
                workspace_path_default=workspace.real_path,
                fd_executable=self.state.api_server_config.fd_executable,
                rg_executable=self.state.api_server_config.rg_executable,
            )
            metadata = RunMetadata(
                workspace_path=workspace.real_path, session_key=session_id
            )

            run_id = create_id("acprun")
            engine = create_runtime_engine(config)

            def permission_requester(request: dict[str, Any]) -> str:
                if auto_allow:
                    return "allow"
                if cancel_event.is_set():
                    return "cancelled"
                return self._request_permission_sync(session_id, request)

            def event_callback(event: dict[str, Any]) -> None:
                etype = str(event.get("type") or "")
                payload = event.get("payload") or {}

                if etype == "delta":
                    text = str(payload.get("text") or "")
                    if not text:
                        return
                    self._emit_session_update_threadsafe(
                        session_id,
                        {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": text},
                        },
                    )
                    return

                if etype == "thinking_delta":
                    text = str(payload.get("text") or "")
                    if not text:
                        return
                    self._emit_session_update_threadsafe(
                        session_id,
                        {
                            "sessionUpdate": "agent_thought_chunk",
                            "content": {"type": "text", "text": text},
                        },
                        record_event=True,
                    )
                    return

                if etype == "available_commands":
                    commands = payload.get("commands")
                    if not isinstance(commands, list):
                        return
                    self._emit_session_update_threadsafe(
                        session_id,
                        {
                            "sessionUpdate": "available_commands_update",
                            "availableCommands": commands,
                        },
                        record_event=False,
                    )
                    return

                if etype != "timeline_entry":
                    return

                entry_kind = str(payload.get("entry_kind") or "")
                if not entry_kind:
                    return

                if entry_kind == "planning":
                    self._emit_session_update_threadsafe(
                        session_id,
                        {
                            "sessionUpdate": "plan",
                            "entries": [
                                {
                                    "content": str(payload.get("content_text") or ""),
                                    "priority": "medium",
                                    "status": "pending",
                                }
                            ],
                        },
                    )
                    return

                if entry_kind != "action":
                    return

                content_json = payload.get("content_json")
                if isinstance(content_json, str):
                    try:
                        content_json = json.loads(content_json)
                    except Exception:
                        content_json = None
                content_json = content_json if isinstance(content_json, dict) else {}
                tool_calls = (
                    content_json.get("toolCalls")
                    if isinstance(content_json, dict)
                    else None
                )
                error_text = (
                    str(content_json.get("error") or "")
                    if isinstance(content_json, dict)
                    else ""
                )
                status = "failed" if error_text else "completed"

                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict):
                            continue
                        tool_call_id = str(tool_call.get("id") or create_id("tool"))
                        name = str(tool_call.get("name") or "Tool call")
                        kind = self._tool_kind_for_name(name)

                        self._emit_session_update_threadsafe(
                            session_id,
                            {
                                "sessionUpdate": "tool_call",
                                "toolCallId": tool_call_id,
                                "title": name,
                                "kind": kind,
                                "status": "pending",
                            },
                        )
                        self._emit_session_update_threadsafe(
                            session_id,
                            {
                                "sessionUpdate": "tool_call_update",
                                "toolCallId": tool_call_id,
                                "status": status,
                            },
                        )

            result = await asyncio.to_thread(
                engine.process,
                run_id,
                user_text,
                {"uid": "acp-user", "tid": "acp-team", "exp_epoch": 0},
                metadata,
                event_callback,
                cancel_event.is_set,
                permission_requester,
            )

            final_answer = str(result.get("final_answer") or "").strip()
            if final_answer:
                self.state.session_service.timeline_service.refresh_session_summary(
                    session_id,
                    active_connection_id=session.active_connection_id,
                    active_model_id=session.active_model_id,
                    trigger_title_generation=True,
                )
                await self._emit_session_update(
                    session_id,
                    {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {
                            "type": "text",
                            "text": final_answer,
                        },
                    },
                )

        except RuntimeCancelledError:
            stop_reason = "cancelled"
        except Exception as exc:  # noqa: BLE001
            if str(exc) == "cancelled":
                stop_reason = "cancelled"
            else:
                await self._send_error(req_id, -32000, str(exc))
                return
        finally:
            self._prompt_tasks.pop(session_id, None)

        await self._send_result(req_id, {"stopReason": stop_reason})

    def _request_permission_sync(self, session_id: str, request: dict[str, Any]) -> str:
        if self.loop is None:
            return "cancelled"

        rpc_id = self._next_client_rpc_id()
        future: Future = Future()
        with self._pending_lock:
            self._pending_client_calls[rpc_id] = _ClientRequestWaiter(
                future=future,
                session_id=session_id,
            )

        tool_call_id = str(request.get("toolCallId") or create_id("tool"))
        title = str(request.get("title") or "Permission required")
        kind = self._permission_kind_for_request(request)

        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "session/request_permission",
            "params": {
                "sessionId": session_id,
                "toolCall": {
                    "toolCallId": tool_call_id,
                    "title": title,
                    "kind": kind,
                    "status": "pending",
                },
                "options": [
                    {
                        "optionId": "allow-once",
                        "name": "Allow once",
                        "kind": "allow_once",
                    },
                    {
                        "optionId": "reject-once",
                        "name": "Reject",
                        "kind": "reject_once",
                    },
                ],
            },
        }

        asyncio.run_coroutine_threadsafe(self._send_json(payload), self.loop).result()
        result = future.result()

        outcome = result.get("outcome") if isinstance(result, dict) else None
        decision = (
            str(outcome.get("outcome") or "") if isinstance(outcome, dict) else ""
        )
        option_id = (
            str(outcome.get("optionId") or "") if isinstance(outcome, dict) else ""
        )

        if decision == "cancelled":
            self._send_permission_terminal_update(
                session_id,
                tool_call_id,
                "cancelled",
            )
            return "cancelled"

        if option_id.startswith("allow"):
            self._send_permission_terminal_update(
                session_id,
                tool_call_id,
                "completed",
            )
            return "allow"

        self._send_permission_terminal_update(
            session_id,
            tool_call_id,
            "failed",
        )
        return "reject"

    async def _request_client_rpc(
        self, session_id: str, method: str, params: dict[str, Any]
    ) -> Any:
        if self.loop is None:
            raise RuntimeError("ACP loop is unavailable")

        rpc_id = self._next_client_rpc_id()
        future: Future = Future()
        with self._pending_lock:
            self._pending_client_calls[rpc_id] = _ClientRequestWaiter(
                future=future,
                session_id=session_id,
            )

        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            "params": params,
        }
        await self._send_json(payload)
        result = await asyncio.wrap_future(future)
        return result

    async def _request_client_read_text_file(self, session_id: str, uri: str) -> str:
        path = self._uri_to_fs_path(uri)
        response = await self._request_client_rpc(
            session_id,
            "fs/read_text_file",
            {"path": path},
        )
        if not isinstance(response, dict):
            return ""

        content = response.get("content")
        if isinstance(content, str):
            return content

        text = response.get("text")
        if isinstance(text, str):
            return text

        return ""

    def _send_permission_terminal_update(
        self,
        session_id: str,
        tool_call_id: str,
        status: str,
    ) -> None:
        self._emit_session_update_threadsafe(
            session_id,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": tool_call_id,
                "status": status,
            },
            record_event=True,
        )

    def _handle_client_response(self, payload: dict[str, Any]) -> None:
        rpc_id = payload.get("id")
        if not isinstance(rpc_id, int):
            return

        with self._pending_lock:
            waiter = self._pending_client_calls.pop(rpc_id, None)
        if waiter is None:
            return

        if "result" in payload:
            waiter.future.set_result(payload.get("result") or {})
            return

        waiter.future.set_result({"outcome": {"outcome": "cancelled"}})

    def _permission_kind_for_request(self, request: dict[str, Any]) -> str:
        kind = str(request.get("kind") or "").strip().lower()
        if kind in {"write", "edit", "python_exec_agent"}:
            return kind
        if kind in {"execute", "python", "code"}:
            return "python_exec_agent"
        return "other"

    def _tool_kind_for_name(self, tool_name: str) -> str:
        lowered = tool_name.strip().lower()
        if lowered in {"write", "edit"}:
            return lowered
        if lowered == "python_exec_agent":
            return "python_exec_agent"
        return "other"

    def _next_client_rpc_id(self) -> int:
        with self._pending_lock:
            self._next_rpc_id += 1
            return self._next_rpc_id

    def _cancel_session(self, session_id: str) -> None:
        if not session_id:
            return

        event = self._session_cancel_events.setdefault(session_id, threading.Event())
        event.set()

        with self._pending_lock:
            to_cancel = [
                key
                for key, waiter in self._pending_client_calls.items()
                if waiter.session_id == session_id
            ]
            for key in to_cancel:
                waiter = self._pending_client_calls.pop(key, None)
                if waiter and not waiter.future.done():
                    waiter.future.set_result({"outcome": {"outcome": "cancelled"}})

    def _cancel_all_sessions(self) -> None:
        session_ids = set(self._session_cancel_events.keys()) | set(
            self._prompt_tasks.keys()
        )
        for session_id in session_ids:
            self._cancel_session(session_id)

    async def _emit_session_update(
        self,
        session_id: str,
        update: dict[str, Any],
        *,
        record_event: bool = True,
    ) -> None:
        payload = {"sessionId": session_id, "update": update}
        if record_event:
            self._persist_session_update_event(payload)
        await self._send_notification("session/update", payload)

    def _emit_session_update_threadsafe(
        self,
        session_id: str,
        update: dict[str, Any],
        *,
        record_event: bool = True,
    ) -> None:
        payload = {"sessionId": session_id, "update": update}
        if record_event:
            self._persist_session_update_event(payload)
        self._send_notification_threadsafe("session/update", payload)

    def _persist_session_update_event(self, params: dict[str, Any]) -> None:
        session_id = str(params.get("sessionId") or "")
        if not session_id:
            return
        update = params.get("update")
        event_type = (
            str(update.get("sessionUpdate") or "session_update")
            if isinstance(update, dict)
            else "session_update"
        )
        payload = {
            "method": "session/update",
            "params": params,
        }
        self.state.repositories.acp_event_log.append(
            session_id=session_id,
            event_type=event_type,
            event_json=json.dumps(payload, ensure_ascii=False),
        )

    def _serialize_event_log(self, event: Any) -> dict[str, Any]:
        payload: dict[str, Any] | None = None
        try:
            parsed = json.loads(event.event_json)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = None
        return {
            "eventId": event.id,
            "sessionId": event.session_id,
            "turnId": event.turn_id,
            "sequenceNo": event.sequence_no,
            "eventType": event.event_type,
            "payload": payload,
            "createdAt": event.created_at,
        }

    async def _send_result(self, req_id: Any, result: dict[str, Any]) -> None:
        await self._send_json({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def _send_error(self, req_id: Any, code: int, message: str) -> None:
        await self._send_json(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": code,
                    "message": message,
                },
            }
        )

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        await self._send_json({"jsonrpc": "2.0", "method": method, "params": params})

    def _send_notification_threadsafe(
        self, method: str, params: dict[str, Any]
    ) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._send_notification(method, params),
            self.loop,
        ).result()

    async def _send_json(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.transport.send_json(payload)
