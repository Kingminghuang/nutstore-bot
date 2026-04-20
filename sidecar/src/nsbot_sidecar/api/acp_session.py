from __future__ import annotations

import base64
from contextlib import suppress
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

import anyio

from nsbot_sidecar.infrastructure.local_paths import nsbot_home
from nsbot_sidecar.infrastructure.repositories import create_id
from nsbot_sidecar.infrastructure.storage import transaction
from nsbot_sidecar.providers.provider_catalog import list_providers
from nsbot_sidecar.runtime.engine import create_runtime_engine
from nsbot_sidecar.runtime.types import (
    RunMetadata,
    RuntimeCancelledError,
    RuntimeWorkerConfig,
)
from nsbot_sidecar.application.timeline_service import serialize_session_summary


_ATTACHMENT_TEXT_MAX_BYTES = 50 * 1024


def _acp_debug_enabled() -> bool:
    value = os.environ.get("NSBOT_ACP_DEBUG", "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def _acp_debug_log(message: str) -> None:
    if _acp_debug_enabled():
        print(f"[acp-session] {message}", file=sys.stderr, flush=True)


@dataclass
class _ClientRequestWaiter:
    event: anyio.Event
    result: dict[str, Any] | None
    session_id: str
    turn_id: str | None = None


@dataclass
class _SessionMcpConnection:
    client: Any
    tools: list[Any]


@dataclass(frozen=True)
class _AuthState:
    method_id: str
    target_provider_id: str
    effective_provider_id: str
    key_source: str
    gateway_protocol: str | None = None
    gateway_base_url: str | None = None


@dataclass(frozen=True)
class _SessionActorMessage:
    kind: str
    req_id: Any | None = None
    params: dict[str, Any] | None = None


class _SessionActor:
    def __init__(self, session_id: str, owner: "AcpJsonRpcSession"):
        self.session_id = session_id
        self.owner = owner
        self._send_stream, self._recv_stream = anyio.create_memory_object_stream(0)
        self._closed: anyio.Event | None = None
        self._running_prompt = False

    @property
    def running_prompt(self) -> bool:
        return self._running_prompt

    def start(self, task_group: anyio.abc.TaskGroup) -> None:
        if self._closed is not None:
            return
        self._closed = anyio.Event()
        task_group.start_soon(self._run)

    async def enqueue_prompt(self, req_id: Any, params: dict[str, Any]) -> None:
        await self._send_stream.send(
            _SessionActorMessage(kind="prompt", req_id=req_id, params=params)
        )

    async def enqueue_edit_and_prompt(self, req_id: Any, params: dict[str, Any]) -> None:
        await self._send_stream.send(
            _SessionActorMessage(
                kind="edit_and_prompt",
                req_id=req_id,
                params=params,
            )
        )

    async def enqueue_cancel(self) -> None:
        await self._send_stream.send(_SessionActorMessage(kind="cancel"))

    async def close(self) -> None:
        with suppress(Exception):
            await self._send_stream.send(_SessionActorMessage(kind="shutdown"))
        with suppress(Exception):
            await self._send_stream.aclose()
        if self._closed is not None:
            with suppress(Exception):
                await self._closed.wait()

    async def _run(self) -> None:
        try:
            async with self._recv_stream:
                async for message in self._recv_stream:
                    if message.kind == "shutdown":
                        break
                    if message.kind == "cancel":
                        self.owner._cancel_session_state(self.session_id)
                        continue
                    if message.kind not in {"prompt", "edit_and_prompt"}:
                        continue
                    req_id = message.req_id
                    params = message.params or {}
                    self._running_prompt = True
                    runner = (
                        self.owner._handle_edit_and_prompt_request
                        if message.kind == "edit_and_prompt"
                        else self.owner._handle_prompt_request
                    )
                    try:
                        await runner(req_id, params)
                    finally:
                        self._running_prompt = False
        finally:
            if self._closed is not None:
                self._closed.set()


class JsonRpcTransport(Protocol):
    async def accept(self) -> None: ...

    async def receive_json(self) -> dict[str, Any]: ...

    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


class StdioJsonRpcTransport:
    async def accept(self) -> None:
        return

    async def receive_json(self) -> dict[str, Any]:
        line = await anyio.to_thread.run_sync(sys.stdin.readline)
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
        self._send_lock = anyio.Lock()
        self._task_group: anyio.abc.TaskGroup | None = None
        self._task_group_cm: Any | None = None

        self._next_rpc_id = 1000
        self._pending_client_calls: dict[int, _ClientRequestWaiter] = {}
        self._session_actors: dict[str, _SessionActor] = {}
        self._session_cancelled: dict[str, bool] = {}
        self._session_mcp_connections: dict[str, _SessionMcpConnection] = {}
        self._turn_plan_entries: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._auth_state: _AuthState | None = None

        self._client_capabilities: dict[str, Any] = {
            "fs": {"readTextFile": False, "writeTextFile": False},
            "terminal": False,
        }

    async def run(self) -> None:
        await self.transport.accept()
        await self._ensure_task_group()
        try:
            while True:
                payload = await self.transport.receive_json()
                await self._dispatch(payload)
        except EOFError:
            self._close_all_mcp_connections()
            self._cancel_all_sessions()
        except RuntimeError as exc:
            # Starlette may raise RuntimeError on abrupt client disconnect in tests/runtime.
            if "WebSocket is not connected" not in str(exc):
                raise
            self._close_all_mcp_connections()
            self._cancel_all_sessions()
        finally:
            await self._close_all_session_actors()
            await self._close_task_group()

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
                await self._send_result(req_id, self._handle_authenticate(params))
                return
            if method == "disconnect":
                await self._send_result(req_id, {})
                self._close_all_mcp_connections()
                self._cancel_all_sessions()
                await self._close_all_session_actors()
                await self._close_task_group()
                await self.transport.close()
                return
            if method == "session/new":
                self._ensure_authenticated()
                await self._send_result(req_id, self._handle_session_new(params))
                return
            if method == "session/list":
                await self._send_result(req_id, self._handle_session_list(params))
                return
            if method == "_nsbot/workspace/list":
                await self._send_result(
                    req_id,
                    self.state.session_service.list_workspaces_payload(),
                )
                return
            if method == "_nsbot/workspace/create":
                await self._send_result(
                    req_id,
                    self.state.session_service.create_workspace(params),
                )
                return
            if method == "_nsbot/workspace/update":
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
            if method == "_nsbot/workspace/delete":
                workspace_id = str(
                    params.get("workspaceId") or params.get("workspace_id") or ""
                )
                if not workspace_id:
                    await self._send_error(req_id, -32000, "workspaceId is required")
                    return
                self.state.session_service.delete_workspace(workspace_id)
                await self._send_result(req_id, {})
                return
            if method == "_nsbot/workspace/sessions/list":
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
            if method == "_nsbot/workspace/sessions/create":
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
            if method == "_nsbot/workspace/sidecar_index/status":
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
            if method == "_nsbot/workspace/find_entries":
                await self._send_result(
                    req_id,
                    self._handle_workspace_find_entries(params),
                )
                return
            if method == "_nsbot/provider/catalog":
                await self._send_result(
                    req_id,
                    self.state.provider_service.catalog_payload(),
                )
                return
            if method == "_nsbot/provider/list":
                await self._send_result(
                    req_id,
                    self.state.provider_service.list_providers_payload(),
                )
                return
            if method == "_nsbot/provider/model_options":
                await self._send_result(
                    req_id,
                    self.state.provider_service.model_options_payload(),
                )
                return
            if method == "_nsbot/provider/create":
                await self._send_result(
                    req_id,
                    self.state.provider_service.create_provider(params),
                )
                return
            if method == "_nsbot/provider/update":
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
            if method == "_nsbot/provider/delete":
                provider_id = str(
                    params.get("providerId") or params.get("provider_id") or ""
                )
                if not provider_id:
                    await self._send_error(req_id, -32000, "providerId is required")
                    return
                self.state.provider_service.delete_provider(provider_id)
                await self._send_result(req_id, {})
                return
            if method == "session/load":
                self._ensure_authenticated()
                await self._send_result(req_id, await self._handle_session_load(params))
                return
            if method == "session/resume":
                self._ensure_authenticated()
                await self._send_result(req_id, self._handle_session_resume(params))
                return
            if method == "_nsbot/session/update_meta":
                session_id = str(
                    params.get("sessionId") or params.get("session_id") or ""
                )
                if not session_id:
                    await self._send_error(req_id, -32000, "sessionId is required")
                    return
                before = self._session_summary_payload(session_id)
                result = self.state.session_service.update_session(session_id, params)
                await self._emit_session_info_update_if_changed(
                    session_id,
                    before,
                    result,
                )
                await self._send_result(req_id, result)
                return
            if method == "_nsbot/session/delete":
                session_id = str(
                    params.get("sessionId") or params.get("session_id") or ""
                )
                if not session_id:
                    await self._send_error(req_id, -32000, "sessionId is required")
                    return
                self.state.session_service.delete_session(session_id)
                await self._send_result(req_id, {})
                return
            if method == "_nsbot/timeline/list":
                await self._send_result(req_id, self._handle_timeline_list(params))
                return
            if method == "_nsbot/draft_attachment/list":
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
            if method == "_nsbot/draft_attachment/delete":
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
            if method == "_nsbot/draft_attachment/create":
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
            if method == "_nsbot/attachment/list":
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
            if method == "_nsbot/attachment/delete":
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
            if method == "_nsbot/attachment/create":
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
                result = self._handle_set_config_option(params)
                session_id = str(params.get("sessionId") or "")
                if session_id:
                    await self._emit_config_option_update(
                        session_id,
                        result.get("configOptions") or [],
                    )
                await self._send_result(req_id, result)
                return
            if method == "session/cancel":
                session_id = str(params.get("sessionId") or "")
                await self._cancel_session(session_id)
                await self._send_result(req_id, {"cancelled": True})
                return
            if method == "session/prompt":
                self._ensure_authenticated()
                session_id = str(params.get("sessionId") or "")
                if not session_id:
                    await self._send_error(req_id, -32000, "sessionId is required")
                    return
                actor = await self._ensure_session_actor(session_id)
                if actor.running_prompt:
                    await self._send_error(req_id, -32000, "session already running")
                    return
                await actor.enqueue_prompt(req_id, params)
                return
            if method == "_nsbot/session/edit_and_prompt":
                session_id = str(params.get("sessionId") or "")
                if not session_id:
                    await self._send_error(req_id, -32000, "sessionId is required")
                    return
                actor = await self._ensure_session_actor(session_id)
                if actor.running_prompt:
                    await self._send_error(req_id, -32000, "session already running")
                    return
                await actor.enqueue_edit_and_prompt(req_id, params)
                return

            _acp_debug_log(f"method not found during request dispatch: {method}")
            await self._send_error(req_id, -32601, f"Method not found: {method}")
        except Exception as exc:  # noqa: BLE001
            await self._send_error(req_id, -32000, str(exc))

    async def _handle_notification(self, payload: dict[str, Any]) -> None:
        method = str(payload.get("method") or "")
        params = payload.get("params") or {}
        if method == "session/cancel":
            session_id = str(params.get("sessionId") or "")
            await self._cancel_session(session_id)

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
                "mcpCapabilities": {
                    "http": True,
                    "sse": False,
                },
                "sessionCapabilities": {
                    "list": {},
                    "resume": {},
                },
                "_meta": {
                    "nsbot": {
                        "extensions": {
                            "workspace": True,
                            "provider": True,
                            "attachment": True,
                            "draft_attachment": True,
                            "timeline": True,
                            "session_edit": True,
                        }
                    }
                },
            },
            "agentInfo": {
                "name": "nutstore-sidecar",
                "title": "Nutstore Sidecar",
                "version": "0.1.0",
            },
            "authMethods": self._supported_auth_methods(),
        }

    def _supported_auth_methods(self) -> list[dict[str, Any]]:
        methods: list[dict[str, Any]] = []
        for provider in list_providers():
            provider_id = str(provider.get("id") or "").strip().lower()
            if not provider_id:
                continue
            provider_label = str(provider.get("label") or provider_id).strip() or provider_id
            methods.append(
                {
                    "id": self._auth_method_id_for_provider(provider_id),
                    "name": f"Use {provider_label}",
                    "description": f"Authenticate with the configured API key for {provider_label}",
                    "_meta": {"api-key": {"provider": provider_id}},
                }
            )

        methods.append(
            {
                "id": "GATEWAY",
                "name": "AI API Gateway",
                "description": "Authenticate with a custom OpenAI-compatible gateway provider",
                "_meta": {"gateway": {"protocol": "custom", "baseUrl": ""}},
            }
        )
        return methods

    def _auth_method_id_for_provider(self, provider_id: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]+", "_", provider_id).strip("_").upper()
        return f"USE_{normalized}"

    def _auth_method_to_provider_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for provider in list_providers():
            provider_id = str(provider.get("id") or "").strip().lower()
            if not provider_id:
                continue
            mapping[self._auth_method_id_for_provider(provider_id)] = provider_id
        return mapping

    def _gateway_provider_id(self, protocol: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", protocol.strip().lower()).strip("-")
        if not slug:
            slug = "custom"
        return f"gateway-{slug}"

    def _ensure_authenticated(self) -> None:
        if self._auth_state is None:
            raise RuntimeError("Authentication required")

    def _extract_meta_api_key(self, meta: Any) -> str | None:
        if not isinstance(meta, dict):
            return None
        api_key_meta = meta.get("api-key")
        if isinstance(api_key_meta, str):
            key = api_key_meta.strip()
            return key or None
        if isinstance(api_key_meta, dict):
            value = api_key_meta.get("value")
            if isinstance(value, str):
                key = value.strip()
                return key or None
        return None

    def _provider_secret_api_key(self, provider_id: str) -> str | None:
        bundle = self.state.repositories.providers.get_bundle_by_id(provider_id)
        if bundle is None:
            return None
        secret_payload = self.state.secret_store.load_provider_secret(
            bundle.provider.secret_ref
        )
        if secret_payload is None:
            return None
        key = str(secret_payload.api_key or "").strip()
        return key or None

    def _resolve_api_key_for_auth(
        self, *, target_provider_id: str, explicit_api_key: str | None
    ) -> tuple[str, str, str]:
        if explicit_api_key:
            return explicit_api_key, "meta", target_provider_id

        env_key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
        if env_key:
            return env_key, "env", target_provider_id

        target_secret = self._provider_secret_api_key(target_provider_id)
        if target_secret:
            return target_secret, "provider_secret", target_provider_id

        fallback_provider_id: str | None = None
        fallback_secret: str | None = None
        try:
            selection = self._default_selection()
            fallback_provider_id = str(selection["providerId"] or "").strip()
            if fallback_provider_id:
                fallback_secret = self._provider_secret_api_key(fallback_provider_id)
        except Exception:
            fallback_provider_id = None
            fallback_secret = None

        if fallback_secret:
            return fallback_secret, "default_provider_secret", fallback_provider_id or target_provider_id

        if fallback_provider_id:
            raise RuntimeError(
                f"API key is missing for provider '{target_provider_id}' and fallback provider '{fallback_provider_id}'"
            )
        raise RuntimeError(f"API key is missing for provider '{target_provider_id}'")

    def _upsert_provider_for_auth(
        self,
        *,
        provider_id: str,
        api_key: str,
        gateway_protocol: str | None = None,
        gateway_base_url: str | None = None,
    ) -> None:
        existing = self.state.repositories.providers.get_bundle_by_id(provider_id)
        if gateway_protocol is not None:
            provider_payload: dict[str, Any] = {
                "kind": "custom",
                "customSlug": provider_id,
                "displayName": f"Gateway ({gateway_protocol})",
                "baseUrl": gateway_base_url,
                "apiKey": api_key,
            }
            if existing is None:
                self.state.provider_service.create_provider(provider_payload)
            else:
                self.state.provider_service.update_provider(provider_id, provider_payload)
            return

        if existing is None:
            self.state.provider_service.create_provider(
                {
                    "kind": "builtin",
                    "catalogProviderId": provider_id,
                    "displayName": provider_id,
                    "apiKey": api_key,
                }
            )
            return

        self.state.provider_service.update_provider(provider_id, {"apiKey": api_key})

    def _handle_authenticate(self, params: dict[str, Any]) -> dict[str, Any]:
        method_id = str(params.get("methodId") or "").strip()
        if not method_id:
            raise RuntimeError("methodId is required")

        supported_methods = self._auth_method_to_provider_map()
        target_provider_id: str
        gateway_protocol: str | None = None
        gateway_base_url: str | None = None

        meta = params.get("_meta")

        if method_id in supported_methods:
            target_provider_id = supported_methods[method_id]
        elif method_id == "GATEWAY":
            gateway_meta = meta.get("gateway") if isinstance(meta, dict) else None
            if not isinstance(gateway_meta, dict):
                raise RuntimeError("Malformed gateway payload: gateway object is required")

            protocol_value = gateway_meta.get("protocol")
            if not isinstance(protocol_value, str) or not protocol_value.strip():
                raise RuntimeError("Malformed gateway payload: gateway.protocol is required")
            gateway_protocol = protocol_value.strip().lower()

            base_url_value = gateway_meta.get("baseUrl")
            if not isinstance(base_url_value, str) or not base_url_value.strip():
                raise RuntimeError("Malformed gateway payload: gateway.baseUrl is required")
            gateway_base_url = base_url_value.strip()

            target_provider_id = self._gateway_provider_id(gateway_protocol)
        else:
            raise RuntimeError("unsupported authentication method")

        explicit_api_key = self._extract_meta_api_key(meta)
        api_key, key_source, effective_provider_id = self._resolve_api_key_for_auth(
            target_provider_id=target_provider_id,
            explicit_api_key=explicit_api_key,
        )

        if method_id == "GATEWAY":
            self._upsert_provider_for_auth(
                provider_id=target_provider_id,
                api_key=api_key,
                gateway_protocol=gateway_protocol,
                gateway_base_url=gateway_base_url,
            )
        elif key_source in {"meta", "env"}:
            self._upsert_provider_for_auth(
                provider_id=target_provider_id,
                api_key=api_key,
            )

        self._auth_state = _AuthState(
            method_id=method_id,
            target_provider_id=target_provider_id,
            effective_provider_id=effective_provider_id,
            key_source=key_source,
            gateway_protocol=gateway_protocol,
            gateway_base_url=gateway_base_url,
        )
        return {
            "_meta": {
                "auth": {
                    "methodId": method_id,
                    "targetProviderId": target_provider_id,
                    "effectiveProviderId": effective_provider_id,
                    "keySource": key_source,
                    "gateway": {
                        "protocol": gateway_protocol,
                        "baseUrl": gateway_base_url,
                    }
                    if gateway_protocol is not None
                    else None,
                }
            }
        }

    def _ensure_session_exists(self, session_id: str):
        return self.state.repositories.sessions.get_by_id(session_id)

    def _find_or_create_workspace(self, cwd: str):
        normalized = str(Path(cwd).expanduser().resolve())
        workspace = self.state.repositories.workspaces.get_by_real_path(normalized)
        if workspace is not None:
            return workspace
        name = Path(normalized).name or "workspace"
        return self.state.repositories.workspaces.create(
            name=name,
            path_label=normalized,
            real_path=normalized,
        )

    def _session_config(self, session_id: str) -> dict[str, Any]:
        session = self.state.repositories.sessions.get_by_id(session_id)
        return dict(session.session_config)

    def _session_summary_payload(self, session_id: str) -> dict[str, Any]:
        session = self.state.repositories.sessions.get_by_id(session_id)
        return serialize_session_summary(session)

    def _session_thought_level(self, session_id: str) -> str | None:
        config = self._session_config(session_id)
        value = config.get("thought_level")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _update_session_config_value(
        self, session_id: str, key: str, value: str | None
    ) -> None:
        session = self.state.repositories.sessions.get_by_id(session_id)
        config = dict(session.session_config)
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value
        self.state.repositories.sessions.touch(session_id, session_config=config)

    def _decode_session_list_cursor(self, cursor: str) -> tuple[str, str]:
        try:
            decoded = base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8")
            payload = json.loads(decoded)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("cursor is invalid") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("cursor is invalid")
        updated_at = payload.get("updatedAt")
        session_id = payload.get("sessionId")
        if not isinstance(updated_at, str) or not updated_at.strip():
            raise RuntimeError("cursor is invalid")
        if not isinstance(session_id, str) or not session_id.strip():
            raise RuntimeError("cursor is invalid")
        return updated_at, session_id

    def _encode_session_list_cursor(self, updated_at: str, session_id: str) -> str:
        payload = json.dumps(
            {"updatedAt": updated_at, "sessionId": session_id},
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8")

    def _default_selection(self) -> dict[str, str]:
        payload = self.state.provider_service.model_options_payload()
        selection = payload.get("defaultSelection")
        if not isinstance(selection, dict):
            raise RuntimeError("No default provider/model available")

        provider_id = str(selection.get("providerId") or "")
        model_id = str(selection.get("modelId") or "")
        if not provider_id or not model_id:
            raise RuntimeError("No default provider/model available")

        return {"providerId": provider_id, "modelId": model_id}

    def _config_options_for_session(self, session_id: str) -> list[dict[str, Any]]:
        session = self.state.repositories.sessions.get_by_id(session_id)
        model_payload = self.state.provider_service.model_options_payload()
        groups = model_payload.get("groups")

        available_models: list[dict[str, Any]] = []
        reasoning_values: list[str] = []
        if isinstance(groups, list):
            for group in groups:
                if str(group.get("providerId") or "") != str(
                    session.active_provider_id or ""
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

        thought_level = self._session_thought_level(session_id)
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
            active_provider_id=selection["providerId"],
            active_model_id=selection["modelId"],
        )
        try:
            self._configure_session_mcp_servers(session.id, params)
        except Exception:
            self._close_session_mcp_connection(session.id)
            with suppress(Exception):
                self.state.repositories.sessions.delete_by_id(session.id)
            raise

        return {
            "sessionId": session.id,
            "configOptions": self._config_options_for_session(session.id),
        }

    def _handle_session_list(self, params: dict[str, Any]) -> dict[str, Any]:
        cwd = str(params.get("cwd") or "").strip()
        workspace_id: str | None = None
        if cwd:
            normalized = str(Path(cwd).expanduser().resolve())
            workspace = self.state.repositories.workspaces.get_by_real_path(normalized)
            if workspace is None:
                return {"sessions": []}
            workspace_id = workspace.id

        cursor_value = str(params.get("cursor") or "").strip()
        cursor_updated_at: str | None = None
        cursor_session_id: str | None = None
        if cursor_value:
            cursor_updated_at, cursor_session_id = self._decode_session_list_cursor(
                cursor_value
            )

        page_size = 100
        page, next_marker = self.state.repositories.sessions.list_page(
            limit=page_size,
            workspace_id=workspace_id,
            cursor_updated_at=cursor_updated_at,
            cursor_id=cursor_session_id,
        )
        workspaces = {ws.id: ws for ws in self.state.repositories.workspaces.list()}
        sessions_payload: list[dict[str, Any]] = []
        for session in page:
            workspace = workspaces.get(session.workspace_id)
            if workspace is None:
                continue
            sessions_payload.append(
                {
                    "sessionId": session.id,
                    "cwd": workspace.real_path,
                    "title": session.title,
                    "updatedAt": session.updated_at,
                }
            )

        result: dict[str, Any] = {"sessions": sessions_payload}
        if next_marker is not None and page:
            last = page[-1]
            result["nextCursor"] = self._encode_session_list_cursor(
                last.updated_at,
                last.id,
            )
        return result

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
        self._configure_session_mcp_servers(session_id, params)
        await self._replay_session_history(session_id)
        return {"configOptions": self._config_options_for_session(session_id)}

    def _handle_session_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = str(params.get("sessionId") or "")
        if not session_id:
            raise RuntimeError("sessionId is required")
        self._ensure_session_exists(session_id)
        self._configure_session_mcp_servers(session_id, params)
        return {"configOptions": self._config_options_for_session(session_id)}

    def _configure_session_mcp_servers(
        self, session_id: str, params: dict[str, Any]
    ) -> None:
        raw_servers = params.get("mcpServers")
        if raw_servers is None:
            return
        if not isinstance(raw_servers, list):
            raise RuntimeError("mcpServers must be an array")

        self._close_session_mcp_connection(session_id)
        if not raw_servers:
            return

        from smolagents import MCPClient

        server_parameters = [
            self._normalize_mcp_server_parameter(server) for server in raw_servers
        ]
        client = MCPClient(server_parameters, structured_output=True)
        tools = list(client.get_tools())
        self._session_mcp_connections[session_id] = _SessionMcpConnection(
            client=client,
            tools=tools,
        )

    def _normalize_mcp_server_parameter(self, value: Any) -> Any:
        if not isinstance(value, dict):
            raise RuntimeError("Each mcpServers entry must be an object")

        transport_type = str(value.get("type") or "").strip().lower()
        if transport_type == "":
            if isinstance(value.get("url"), str) and str(value.get("url")).strip():
                transport_type = "http"
            else:
                transport_type = "stdio"

        if transport_type == "stdio":
            command = str(value.get("command") or "").strip()
            if not command:
                raise RuntimeError("stdio MCP server requires command")

            raw_args = value.get("args")
            if raw_args is None:
                args: list[str] = []
            elif isinstance(raw_args, list):
                args = [str(item) for item in raw_args]
            else:
                raise RuntimeError("stdio MCP server args must be an array")

            raw_env = value.get("env")
            env_map: dict[str, str] = {}
            if raw_env is not None:
                if not isinstance(raw_env, list):
                    raise RuntimeError("stdio MCP server env must be an array")
                for item in raw_env:
                    if not isinstance(item, dict):
                        raise RuntimeError("stdio MCP env entries must be objects")
                    key = str(item.get("name") or "").strip()
                    if not key:
                        raise RuntimeError("stdio MCP env entry name is required")
                    env_map[key] = str(item.get("value") or "")

            from mcp import StdioServerParameters

            return StdioServerParameters(
                command=command,
                args=args,
                env=env_map or None,
            )

        if transport_type == "http":
            url = str(value.get("url") or "").strip()
            if not url:
                raise RuntimeError("http MCP server requires url")

            raw_headers = value.get("headers")
            headers: dict[str, str] = {}
            if raw_headers is not None:
                if not isinstance(raw_headers, list):
                    raise RuntimeError("http MCP server headers must be an array")
                for item in raw_headers:
                    if not isinstance(item, dict):
                        raise RuntimeError("http MCP header entries must be objects")
                    name = str(item.get("name") or "").strip()
                    if not name:
                        raise RuntimeError("http MCP header name is required")
                    headers[name] = str(item.get("value") or "")

            return {
                "url": url,
                "transport": "streamable-http",
                "headers": headers,
            }

        if transport_type == "sse":
            raise RuntimeError("MCP transport 'sse' is not supported")

        raise RuntimeError(f"Unsupported MCP transport type: {transport_type}")

    def _session_mcp_tools(self, session_id: str) -> list[Any]:
        connection = self._session_mcp_connections.get(session_id)
        if connection is None:
            return []
        return list(connection.tools)

    def _close_session_mcp_connection(self, session_id: str) -> None:
        connection = self._session_mcp_connections.pop(session_id, None)
        if connection is None:
            return
        with suppress(Exception):
            connection.client.disconnect()

    def _close_all_mcp_connections(self) -> None:
        for session_id in list(self._session_mcp_connections.keys()):
            self._close_session_mcp_connection(session_id)

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
            self._update_session_config_value(session_id, "thought_level", value)
        else:
            raise RuntimeError(f"Unsupported config option: {config_id}")

        return {"configOptions": self._config_options_for_session(session_id)}

    def _handle_workspace_find_entries(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace_id = str(params.get("workspaceId") or params.get("workspace_id") or "").strip()
        query = str(params.get("query") or "").strip()
        limit_value = params.get("limit")
        limit = 8
        if isinstance(limit_value, int):
            limit = max(1, min(limit_value, 50))
        elif isinstance(limit_value, str) and limit_value.strip().isdigit():
            limit = max(1, min(int(limit_value), 50))

        if not workspace_id:
            raise RuntimeError("workspaceId is required")
        if len(query) < 1:
            raise RuntimeError("query must contain at least one character")

        try:
            workspace = self.state.repositories.workspaces.get_by_id(workspace_id)
        except ValueError as exc:
            raise RuntimeError("Workspace not found") from exc

        workspace_root = Path(workspace.real_path).expanduser().resolve()
        if not workspace_root.exists() or not workspace_root.is_dir():
            raise RuntimeError("Workspace directory is unavailable")

        return {
            "entries": self._find_workspace_entries(
                workspace_root=workspace_root,
                query=query,
                limit=limit,
            )
        }

    def _plan_entries_key(self, session_id: str, turn_id: str) -> tuple[str, str]:
        return (session_id, turn_id)

    async def _emit_full_plan_update(
        self,
        session_id: str,
        turn_id: str,
        entries: list[dict[str, Any]],
    ) -> None:
        await self._emit_session_update(
            session_id,
            {
                "sessionUpdate": "plan",
                "entries": entries,
            },
            turn_id=turn_id,
        )

    def _tool_call_raw_input(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        arguments_text = tool_call.get("argumentsText")
        parsed_arguments: Any = arguments_text
        if isinstance(arguments_text, str):
            try:
                parsed_arguments = json.loads(arguments_text)
            except Exception:
                parsed_arguments = arguments_text
        return {
            "name": str(tool_call.get("name") or "Tool call"),
            "arguments": parsed_arguments,
        }

    def _workspace_root_for_session(self, session_id: str) -> Path | None:
        try:
            session = self.state.repositories.sessions.get_by_id(session_id)
            workspace = self.state.repositories.workspaces.get_by_id(session.workspace_id)
            return Path(workspace.real_path).expanduser().resolve()
        except Exception:
            return None

    def _resolve_tool_path(
        self,
        value: Any,
        *,
        workspace_root: Path | None,
    ) -> str | None:
        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if candidate == "":
            return None
        path = Path(candidate).expanduser()
        if not path.is_absolute() and workspace_root is not None:
            path = workspace_root / path
        with suppress(Exception):
            return str(path.resolve())
        return str(path)

    def _tool_call_locations(
        self,
        *,
        name: str,
        raw_input: dict[str, Any],
        details: dict[str, Any] | None,
        workspace_root: Path | None,
    ) -> list[dict[str, Any]]:
        lowered = name.strip().lower()
        arguments = raw_input.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        path_value: Any = arguments.get("path")
        if lowered in {"ls", "find", "grep"} and path_value in {None, ""}:
            path_value = "."
        resolved = self._resolve_tool_path(path_value, workspace_root=workspace_root)
        if resolved is None:
            return []

        location: dict[str, Any] = {"path": resolved}
        if lowered == "read":
            offset_value = arguments.get("offset")
            if isinstance(offset_value, int) and offset_value >= 1:
                location["line"] = offset_value

        first_changed_line = None
        if isinstance(details, dict):
            candidate = details.get("firstChangedLine")
            if isinstance(candidate, int) and candidate >= 1:
                first_changed_line = candidate
        if first_changed_line is not None and not isinstance(location.get("line"), int):
            location["line"] = first_changed_line
        return [location]

    def _tool_call_summary_text(
        self,
        *,
        name: str,
        raw_input: dict[str, Any],
    ) -> str | None:
        lowered = name.strip().lower()
        arguments = raw_input.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        path_value = arguments.get("path")
        path_label = str(path_value).strip() if path_value is not None else ""

        if lowered == "read":
            offset_value = arguments.get("offset")
            if isinstance(offset_value, int) and offset_value >= 1 and path_label:
                return f"Read {path_label} starting at line {offset_value}."
            if path_label:
                return f"Read {path_label}."
        if lowered == "write" and path_label:
            return f"Wrote {path_label}."
        if lowered == "edit" and path_label:
            return f"Edited {path_label}."
        if lowered == "grep":
            pattern = arguments.get("pattern")
            if isinstance(pattern, str) and pattern.strip():
                if path_label:
                    return f'Searched {path_label} for "{pattern}".'
                return f'Searched for "{pattern}".'
            if path_label:
                return f"Searched {path_label}."
        if lowered == "find":
            pattern = arguments.get("pattern")
            if isinstance(pattern, str) and pattern.strip():
                if path_label:
                    return f'Found paths in {path_label} using "{pattern}".'
                return f'Found paths using "{pattern}".'
            if path_label:
                return f"Found paths in {path_label}."
        if lowered == "ls":
            if path_label:
                return f"Listed {path_label}."
            return "Listed workspace entries."
        return None

    def _tool_call_detail_notice(
        self,
        *,
        name: str,
        raw_input: dict[str, Any],
        details: dict[str, Any] | None,
    ) -> str | None:
        if not isinstance(details, dict):
            return None
        truncation = details.get("truncation")
        if not isinstance(truncation, dict):
            return None
        if not bool(truncation.get("truncated")):
            return None

        lowered = name.strip().lower()
        if lowered != "read":
            return "Output was truncated."

        arguments = raw_input.get("arguments")
        if not isinstance(arguments, dict):
            return "Output was truncated."
        offset_value = arguments.get("offset")
        output_lines = truncation.get("outputLines")
        if not isinstance(offset_value, int) or offset_value < 1:
            return "Output was truncated."
        if not isinstance(output_lines, int) or output_lines < 0:
            return "Output was truncated."
        return f"Output was truncated. Continue with offset={offset_value + output_lines}."

    def _tool_call_update_content(
        self,
        *,
        name: str,
        raw_input: dict[str, Any],
        raw_output: dict[str, Any],
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        summary_text = self._tool_call_summary_text(name=name, raw_input=raw_input)
        if summary_text:
            content.append(
                {
                    "type": "content",
                    "content": {"type": "text", "text": summary_text},
                }
            )

        tool_result = raw_output.get("toolResult")
        per_call_text_blocks: list[str] = []
        if isinstance(tool_result, dict):
            result_content = tool_result.get("content")
            if isinstance(result_content, list):
                for item in result_content:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("type") or "").strip().lower() != "text":
                        continue
                    text = str(item.get("text") or "").strip()
                    if text:
                        per_call_text_blocks.append(text)

        if per_call_text_blocks:
            for block in per_call_text_blocks:
                content.append(
                    {
                        "type": "content",
                        "content": {"type": "text", "text": block},
                    }
                )
        else:
            observations = raw_output.get("observations")
            if isinstance(observations, list) and observations:
                observation_text = "\n".join(
                    str(item).strip() for item in observations if str(item).strip()
                ).strip()
                if observation_text:
                    content.append(
                        {
                            "type": "content",
                            "content": {"type": "text", "text": observation_text},
                        }
                    )

            action_output = raw_output.get("actionOutput")
            if action_output not in (None, "", []):
                if not isinstance(action_output, str):
                    try:
                        action_output = json.dumps(action_output, ensure_ascii=False)
                    except Exception:
                        action_output = str(action_output)
                if str(action_output).strip():
                    content.append(
                        {
                            "type": "content",
                            "content": {
                                "type": "text",
                                "text": str(action_output).strip(),
                            },
                        }
                    )

        detail_notice = self._tool_call_detail_notice(
            name=name,
            raw_input=raw_input,
            details=raw_output.get("details")
            if isinstance(raw_output.get("details"), dict)
            else None,
        )
        if detail_notice:
            content.append(
                {
                    "type": "content",
                    "content": {"type": "text", "text": detail_notice},
                }
            )

        error_text = ""
        if isinstance(tool_result, dict):
            error_payload = tool_result.get("error")
            if isinstance(error_payload, dict):
                message = str(error_payload.get("message") or "").strip()
                if message:
                    error_text = message
            elif error_payload not in (None, "", {}):
                error_text = str(error_payload).strip()
        if not error_text:
            error_text = str(raw_output.get("error") or "").strip()
        if error_text:
            content.append(
                {
                    "type": "content",
                    "content": {"type": "text", "text": error_text},
                }
            )
        return content

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
                resource_text, normalized_resource = self._normalize_embedded_resource(
                    session_id, resource
                )
                if resource_text:
                    parts.append(resource_text)
                normalized_blocks.append(
                    {"type": "resource", "resource": normalized_resource}
                    if normalized_resource is not None
                    else block
                )
                continue

            if block_type == "resource_link":
                uri = str(block.get("uri") or "").strip()
                name = str(block.get("name") or "").strip()
                if not name:
                    name = self._resource_name_from_uri(uri)
                normalized_link = {
                    "type": "resource_link",
                    "uri": uri,
                    "name": name or "resource",
                }
                mime_type = block.get("mimeType") or block.get("mime_type")
                if isinstance(mime_type, str) and mime_type.strip():
                    normalized_link["mimeType"] = mime_type
                for key in ("title", "description", "annotations"):
                    value = block.get(key)
                    if value is not None:
                        normalized_link[key] = value
                size_value = block.get("size")
                if isinstance(size_value, int) and size_value >= 0:
                    normalized_link["size"] = size_value
                elif isinstance(size_value, str) and size_value.strip().isdigit():
                    normalized_link["size"] = int(size_value)
                resource_link_text = self._resource_link_prompt_text(normalized_link)
                if resource_link_text:
                    parts.append(resource_link_text)
                normalized_blocks.append(normalized_link)
                continue

            normalized_blocks.append(block)

        return "\n".join(
            [part for part in parts if part.strip()]
        ).strip(), normalized_blocks

    def _normalize_embedded_resource(
        self, session_id: str, resource: Any
    ) -> tuple[str, dict[str, Any] | None]:
        if not isinstance(resource, dict):
            return "", None

        uri = str(resource.get("uri") or "").strip()
        if self._looks_like_attachment_uri(uri):
            return self._attachment_resource_text(session_id, uri, resource)

        return self._resource_text_from_embedded(resource), resource

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

    def _looks_like_attachment_uri(self, uri: str) -> bool:
        return urlparse(uri).scheme == "attachment"

    def _attachment_resource_text(
        self, session_id: str, uri: str, resource: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        session = self._ensure_session_exists(session_id)
        parsed = urlparse(uri)
        attachment_kind = parsed.netloc.strip().lower()
        attachment_id = parsed.path.lstrip("/").strip()
        if not attachment_id:
            raise RuntimeError("Attachment resource URI is invalid")

        file_name = "attachment"
        mime_type = str(resource.get("mimeType") or "").strip() or None
        absolute_path: Path | None = None

        if attachment_kind == "session":
            try:
                record = self.state.repositories.attachments.get_by_id(attachment_id)
            except ValueError as exc:
                raise RuntimeError("Attachment not found") from exc
            if record.session_id != session_id:
                raise RuntimeError("Attachment not found")
            file_name = record.file_name
            mime_type = mime_type or record.mime_type or None
            absolute_path = self.state.session_service.attachment_store.absolute_path(
                record.storage_path
            )
        elif attachment_kind == "draft":
            try:
                record = self.state.repositories.draft_attachments.get_by_id(
                    attachment_id
                )
            except ValueError as exc:
                raise RuntimeError("Draft attachment not found") from exc
            if record.workspace_id != session.workspace_id:
                raise RuntimeError("Draft attachment not found")
            file_name = record.file_name
            mime_type = mime_type or record.mime_type or None
            absolute_path = self.state.session_service.attachment_store.absolute_path(
                record.storage_path
            )
        else:
            raise RuntimeError("Attachment resource URI is invalid")

        if absolute_path is None or not absolute_path.exists():
            raise RuntimeError("Attachment file is missing")

        normalized_resource: dict[str, Any] = {
            "uri": uri,
            "mimeType": mime_type
            or mimetypes.guess_type(str(absolute_path))[0]
            or "application/octet-stream",
            "title": str(resource.get("title") or "").strip() or file_name,
        }

        text_content, truncated = self._read_attachment_text(absolute_path)
        if text_content is not None:
            normalized_resource["text"] = text_content
            suffix = "\n[Attachment text truncated to 50KB.]" if truncated else ""
            return f"Attached file {file_name}:\n{text_content}{suffix}", normalized_resource

        return (
            f"Attached file {file_name} ({normalized_resource['mimeType']}) is available as an embedded resource.",
            normalized_resource,
        )

    def _read_attachment_text(self, path: Path) -> tuple[str | None, bool]:
        raw = path.read_bytes()
        truncated = len(raw) > _ATTACHMENT_TEXT_MAX_BYTES
        sample = raw[:_ATTACHMENT_TEXT_MAX_BYTES]
        try:
            text = sample.decode("utf-8")
        except UnicodeDecodeError:
            return None, False
        return text, truncated

    def _looks_like_file_uri(self, uri: str) -> bool:
        if uri.startswith("file://"):
            return True
        parsed = urlparse(uri)
        return parsed.scheme == "" and (uri.startswith("/") or uri.startswith("~"))

    def _pick_fd_executable(self) -> str:
        configured = str(self.state.acp_app_config.fd_executable or "").strip()
        if configured:
            return configured
        return shutil.which("fd") or shutil.which("fdfind") or ""

    def _find_workspace_entries(
        self, workspace_root: Path, query: str, limit: int
    ) -> list[dict[str, Any]]:
        fd_executable = self._pick_fd_executable()
        if fd_executable == "":
            raise RuntimeError("fd executable not found")

        pattern = f".*{re.escape(query)}.*"
        cmd = [
            fd_executable,
            pattern,
            ".",
            "--regex",
            "--color=never",
            "--max-results",
            str(max(limit * 4, limit)),
            "--exclude",
            ".git",
            "--exclude",
            "node_modules",
        ]
        result = subprocess.run(
            cmd,
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode not in {0, 1}:
            stderr = result.stderr.strip()
            raise RuntimeError(stderr or "fd command failed")

        normalized_query = query.casefold()
        entries: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for raw_line in result.stdout.splitlines():
            rel_path = raw_line.strip()
            if rel_path == "":
                continue
            full_path = (workspace_root / rel_path).resolve()
            if not str(full_path).startswith(str(workspace_root)):
                continue
            if not full_path.exists():
                continue

            relative_path = full_path.relative_to(workspace_root).as_posix()
            dedupe_key = f"{relative_path}/" if full_path.is_dir() else relative_path
            if dedupe_key in seen_paths:
                continue
            seen_paths.add(dedupe_key)

            name = full_path.name or workspace_root.name
            relative_display = f"{relative_path}/" if full_path.is_dir() else relative_path
            parent_path = Path(relative_path).parent.as_posix()
            if parent_path == ".":
                parent_path = ""
            entries.append(
                {
                    "name": name,
                    "relativePath": relative_display,
                    "parentPath": parent_path,
                    "absolutePath": str(full_path),
                    "uri": full_path.as_uri(),
                    "entryType": "directory" if full_path.is_dir() else "file",
                }
            )

        def _score(item: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
            name = str(item.get("name") or "")
            relative_path = str(item.get("relativePath") or "")
            name_lower = name.casefold()
            path_lower = relative_path.casefold()
            path_parts = [part.casefold() for part in Path(relative_path.rstrip("/")).parts]

            if name_lower == normalized_query:
                match_rank = 0
            elif name_lower.startswith(normalized_query):
                match_rank = 1
            elif any(part.startswith(normalized_query) for part in path_parts):
                match_rank = 2
            elif normalized_query in name_lower:
                match_rank = 3
            else:
                match_rank = 4

            file_rank = 0 if item.get("entryType") == "file" else 1
            name_index = name_lower.find(normalized_query)
            path_index = path_lower.find(normalized_query)
            first_match = name_index if name_index >= 0 else path_index if path_index >= 0 else 10_000
            depth = relative_path.count("/")
            return (match_rank, file_rank, first_match, depth, len(path_lower), path_lower)

        entries.sort(key=_score)
        return entries[:limit]

    def _uri_to_fs_path(self, uri: str) -> str:
        if uri.startswith("file://"):
            parsed = urlparse(uri)
            path = unquote(parsed.path)
            if parsed.netloc and parsed.netloc not in {"", "localhost"}:
                path = f"//{parsed.netloc}{path}"
            return str(Path(path).expanduser())
        return str(Path(uri).expanduser())

    def _resource_name_from_uri(self, uri: str) -> str:
        if not uri:
            return "resource"
        parsed = urlparse(uri)
        path = parsed.path if parsed.scheme else uri
        candidate = Path(unquote(path)).name.strip()
        return candidate or "resource"

    def _resource_link_prompt_text(self, block: dict[str, Any]) -> str:
        uri = str(block.get("uri") or "").strip()
        if not uri:
            return ""

        label = str(block.get("title") or block.get("name") or "").strip()
        description = str(block.get("description") or "").strip()
        mime_type = str(block.get("mimeType") or block.get("mime_type") or "").strip()
        size_value = block.get("size")
        size = size_value if isinstance(size_value, int) and size_value >= 0 else None

        sentences: list[str] = []
        if self._looks_like_file_uri(uri):
            absolute_path = self._uri_to_fs_path(uri)
            path_link = f"[{absolute_path}]({absolute_path})"
            sentences.append(
                f"Referenced workspace entry {path_link}. The agent can inspect this path directly if needed."
            )
            basename = Path(absolute_path).name.strip()
            if label and label != absolute_path and label != basename:
                sentences.append(f"Display label: {label}.")
        else:
            resource_label = label or self._resource_name_from_uri(uri)
            sentences.append(f"Referenced resource {resource_label} at {uri}.")

        if description:
            sentences.append(description if description.endswith(".") else f"{description}.")
        if mime_type:
            sentences.append(f"MIME type: {mime_type}.")
        if size is not None:
            sentences.append(f"Size: {size} bytes.")

        return " ".join(sentence.strip() for sentence in sentences if sentence.strip())

    def _display_text_from_prompt_blocks(self, blocks: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    parts.append(text)
                continue
            if block_type == "resource_link":
                label = str(block.get("title") or block.get("name") or "").strip()
                if label:
                    parts.append(label)
                continue
            if block_type == "resource":
                resource = block.get("resource")
                if isinstance(resource, dict):
                    label = str(resource.get("title") or "").strip()
                    if label:
                        parts.append(label)
        return "\n".join(parts).strip()

    def _editable_text_from_prompt_blocks(self, blocks: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "") != "text":
                continue
            text = str(block.get("text") or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts).strip()

    async def _handle_edit_and_prompt_request(
        self, req_id: Any, params: dict[str, Any]
    ) -> None:
        session_id = str(params.get("sessionId") or "")
        event_id = str(params.get("eventId") or params.get("event_id") or "")
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
                    active_provider_id=session.active_provider_id,
                    active_model_id=session.active_model_id,
                )

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
        latest_user_event_id = self._latest_editable_user_event_id(session_id)
        if latest_user_event_id != event.id:
            raise RuntimeError("Only the latest user input event can be edited")
        message_text = str(
            content.get("editableText")
            or content.get("displayText")
            or content.get("text")
            or ""
        )
        return {"sequenceNo": event.sequence_no, "messageText": message_text}

    def _latest_editable_user_event_id(self, session_id: str) -> str | None:
        events = self.state.repositories.acp_event_log.list_by_session_id(session_id)
        latest_event_id: str | None = None
        for event in events:
            try:
                payload = json.loads(event.event_json)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(payload, dict):
                continue
            params = payload.get("params")
            if not isinstance(params, dict):
                continue
            update = params.get("update")
            if not isinstance(update, dict):
                continue
            if str(update.get("sessionUpdate") or "") != "user_message_chunk":
                continue
            content = update.get("content")
            if not isinstance(content, dict):
                continue
            if str(content.get("type") or "") != "text":
                continue
            latest_event_id = event.id
        return latest_event_id

    async def _handle_prompt_request(self, req_id: Any, params: dict[str, Any]) -> None:
        session_id = str(params.get("sessionId") or "")
        stop_reason = "end_turn"
        turn_id: str | None = None

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

            self._session_cancelled[session_id] = False

            turn_id = create_id("acpturn")
            self._turn_plan_entries[self._plan_entries_key(session_id, turn_id)] = []

            before_summary = self._session_summary_payload(session_id)
            refreshed_summary = self.state.session_service.timeline_service.refresh_session_summary(
                session_id,
                active_provider_id=session.active_provider_id,
                active_model_id=session.active_model_id,
            )
            await self._emit_session_info_update_if_changed(
                session_id,
                before_summary,
                refreshed_summary,
                turn_id=turn_id,
            )

            await self._emit_session_update(
                session_id,
                {
                    "sessionUpdate": "user_message_chunk",
                    "content": {
                        "type": "text",
                        "text": user_text,
                        "displayText": self._display_text_from_prompt_blocks(
                            normalized_blocks
                        )
                        or user_text,
                        "editableText": self._editable_text_from_prompt_blocks(
                            normalized_blocks
                        )
                        or user_text,
                        "promptBlocks": normalized_blocks,
                    },
                },
                turn_id=turn_id,
            )

            bundle = self.state.repositories.providers.get_bundle_by_id_or_raise(
                str(session.active_provider_id or "")
            )
            secret_payload = self.state.secret_store.load_provider_secret(
                bundle.provider.secret_ref
            )
            api_key = secret_payload.api_key if secret_payload is not None else None
            if not api_key:
                raise RuntimeError("Provider is missing an API key")

            thought_level = self._session_thought_level(session_id)
            if not thought_level and isinstance(meta, dict):
                selected = meta.get("selectedReasoningEffort")
                if isinstance(selected, str) and selected.strip():
                    thought_level = selected.strip()

            model_id = str(session.active_model_id or "")
            config = RuntimeWorkerConfig(
                model_id=model_id,
                allow_console_output=False,
                provider=bundle.provider.runtime_provider,
                base_url=bundle.provider.base_url,
                api_key=api_key,
                model=model_id,
                direct_reasoning_effort=thought_level,
                ns_bot_home=str(nsbot_home(self.state.acp_app_config.ns_bot_home)),
                workspace_path_default=workspace.real_path,
                fd_executable=self.state.acp_app_config.fd_executable,
                rg_executable=self.state.acp_app_config.rg_executable,
            )
            metadata = RunMetadata(
                workspace_path=workspace.real_path, session_key=session_id
            )

            engine = create_runtime_engine(
                config,
                extra_tools=self._session_mcp_tools(session_id),
            )

            def permission_requester(request: dict[str, Any]) -> str:
                enriched_request = {
                    **request,
                    "turn_id": turn_id,
                    "turnId": turn_id,
                }
                if auto_allow:
                    return "allow"
                if self._session_cancelled.get(session_id, False):
                    return "cancelled"
                return anyio.from_thread.run(
                    self._request_permission,
                    session_id,
                    enriched_request,
                )

            def event_callback(event: dict[str, Any]) -> None:
                anyio.from_thread.run(
                    self._handle_runtime_event,
                    session_id,
                    turn_id,
                    event,
                )

            result = await engine.process_async(
                turn_id,
                user_text,
                {"uid": "acp-user", "tid": "acp-team", "exp_epoch": 0},
                metadata,
                event_callback,
                lambda: self._session_cancelled.get(session_id, False),
                permission_requester,
            )

            final_answer = str(result.get("final_answer") or "").strip()
            if final_answer:
                plan_key = self._plan_entries_key(session_id, turn_id)
                plan_entries = self._turn_plan_entries.get(plan_key) or []
                if plan_entries:
                    completed_entries = [
                        {**entry, "status": "completed"} for entry in plan_entries
                    ]
                    self._turn_plan_entries[plan_key] = completed_entries
                    await self._emit_full_plan_update(
                        session_id,
                        turn_id,
                        completed_entries,
                    )
                before_summary = self._session_summary_payload(session_id)
                refreshed_summary = self.state.session_service.timeline_service.refresh_session_summary(
                    session_id,
                    active_provider_id=session.active_provider_id,
                    active_model_id=session.active_model_id,
                    trigger_title_generation=True,
                )
                await self._emit_session_info_update_if_changed(
                    session_id,
                    before_summary,
                    refreshed_summary,
                    turn_id=turn_id,
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
                    turn_id=turn_id,
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
            if turn_id is not None:
                self._turn_plan_entries.pop(
                    self._plan_entries_key(session_id, turn_id), None
                )
        await self._send_result(req_id, {"stopReason": stop_reason})

    async def _handle_runtime_event(
        self, session_id: str, turn_id: str, event: dict[str, Any]
    ) -> None:
        etype = str(event.get("type") or "")
        payload = event.get("payload") or {}

        if etype == "delta":
            text = str(payload.get("text") or "")
            if not text:
                return
            await self._emit_session_update(
                session_id,
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": text},
                },
                turn_id=turn_id,
            )
            return

        if etype == "thinking_delta":
            text = str(payload.get("text") or "")
            if not text:
                return
            await self._emit_session_update(
                session_id,
                {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": text},
                },
                turn_id=turn_id,
                record_event=True,
            )
            return

        if etype == "available_commands":
            commands = payload.get("commands")
            if not isinstance(commands, list):
                return
            await self._emit_session_update(
                session_id,
                {
                    "sessionUpdate": "available_commands_update",
                    "availableCommands": commands,
                },
                turn_id=turn_id,
                record_event=False,
            )
            return

        if etype != "timeline_entry":
            return

        entry_kind = str(payload.get("entry_kind") or "")
        if not entry_kind:
            return

        if entry_kind == "planning":
            plan_key = self._plan_entries_key(session_id, turn_id)
            entries = list(self._turn_plan_entries.get(plan_key) or [])
            entries.append(
                {
                    "content": str(payload.get("content_text") or ""),
                    "priority": "medium",
                    "status": "pending",
                }
            )
            self._turn_plan_entries[plan_key] = entries
            await self._emit_full_plan_update(session_id, turn_id, entries)
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
        tool_details_by_call_id = content_json.get("toolDetailsByCallId")
        if not isinstance(tool_details_by_call_id, dict):
            tool_details_by_call_id = {}
        tool_results_by_call_id: dict[str, dict[str, Any]] = {}
        tool_results = content_json.get("toolResults")
        if isinstance(tool_results, list):
            for item in tool_results:
                if not isinstance(item, dict):
                    continue
                call_id = str(item.get("callId") or "").strip()
                if call_id == "":
                    continue
                payload = {k: v for k, v in item.items() if k != "callId"}
                tool_results_by_call_id[call_id] = payload
        tool_results_by_call_id_raw = content_json.get("toolResultsByCallId")
        if isinstance(tool_results_by_call_id_raw, dict):
            for key, value in tool_results_by_call_id_raw.items():
                if not isinstance(key, str) or key.strip() == "":
                    continue
                if isinstance(value, dict):
                    tool_results_by_call_id[key] = value
        workspace_root = self._workspace_root_for_session(session_id)

        if not isinstance(tool_calls, list):
            return
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = str(tool_call.get("id") or create_id("tool"))
            name = str(tool_call.get("name") or "Tool call")
            kind = self._tool_kind_for_name(name)
            raw_input = self._tool_call_raw_input(tool_call)
            tool_result = tool_results_by_call_id.get(tool_call_id)
            if not isinstance(tool_result, dict):
                tool_result = None
            call_detail_payload = tool_details_by_call_id.get(tool_call_id)
            details = None
            if isinstance(tool_result, dict):
                maybe_details = tool_result.get("details")
                if isinstance(maybe_details, dict) and maybe_details:
                    details = maybe_details
            if isinstance(call_detail_payload, dict):
                maybe_details = call_detail_payload.get("details")
                if details is None and isinstance(maybe_details, dict) and maybe_details:
                    details = maybe_details
            raw_output = {
                "observations": content_json.get("observations") or [],
                "actionOutput": content_json.get("actionOutput"),
                "error": content_json.get("error"),
                "usage": content_json.get("usage") or {},
                "durationMs": content_json.get("durationMs") or 0,
                "toolResult": tool_result,
                "details": details,
                "locations": self._tool_call_locations(
                    name=name,
                    raw_input=raw_input,
                    details=details,
                    workspace_root=workspace_root,
                ),
            }
            update_content = self._tool_call_update_content(
                name=name,
                raw_input=raw_input,
                raw_output=raw_output,
            )

            await self._emit_session_update(
                session_id,
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": tool_call_id,
                    "title": name,
                    "kind": kind,
                    "status": "pending",
                    "rawInput": raw_input,
                },
                turn_id=turn_id,
            )
            await self._emit_session_update(
                session_id,
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": tool_call_id,
                    "status": "in_progress",
                    "rawInput": raw_input,
                },
                turn_id=turn_id,
            )
            await self._emit_session_update(
                session_id,
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": tool_call_id,
                    "status": status,
                    "content": update_content,
                    "rawOutput": raw_output,
                },
                turn_id=turn_id,
            )

    async def _request_permission(self, session_id: str, request: dict[str, Any]) -> str:
        rpc_id = self._next_client_rpc_id()
        waiter = _ClientRequestWaiter(
            event=anyio.Event(),
            result=None,
            session_id=session_id,
            turn_id=str(request.get("turn_id") or request.get("turnId") or "") or None,
        )
        turn_id = str(request.get("turn_id") or request.get("turnId") or "") or None
        self._pending_client_calls[rpc_id] = waiter

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

        await self._send_json(payload)
        await waiter.event.wait()
        result = waiter.result or {}

        outcome = result.get("outcome") if isinstance(result, dict) else None
        decision = (
            str(outcome.get("outcome") or "") if isinstance(outcome, dict) else ""
        )
        option_id = (
            str(outcome.get("optionId") or "") if isinstance(outcome, dict) else ""
        )

        if decision == "cancelled":
            await self._send_permission_terminal_update(
                session_id,
                tool_call_id,
                "cancelled",
                turn_id,
            )
            return "cancelled"

        if option_id.startswith("allow"):
            await self._send_permission_terminal_update(
                session_id,
                tool_call_id,
                "completed",
                turn_id,
            )
            return "allow"

        await self._send_permission_terminal_update(
            session_id,
            tool_call_id,
            "failed",
            turn_id,
        )
        return "reject"

    async def _send_permission_terminal_update(
        self,
        session_id: str,
        tool_call_id: str,
        status: str,
        turn_id: str | None = None,
    ) -> None:
        await self._emit_session_update(
            session_id,
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": tool_call_id,
                "status": status,
            },
            turn_id=turn_id,
            record_event=True,
        )

    def _handle_client_response(self, payload: dict[str, Any]) -> None:
        rpc_id = payload.get("id")
        if not isinstance(rpc_id, int):
            return

        waiter = self._pending_client_calls.pop(rpc_id, None)
        if waiter is None:
            return

        if "result" in payload:
            waiter.result = payload.get("result") or {}
            waiter.event.set()
            return

        waiter.result = {"outcome": {"outcome": "cancelled"}}
        waiter.event.set()

    def _permission_kind_for_request(self, request: dict[str, Any]) -> str:
        kind = str(request.get("kind") or "").strip().lower()
        if kind in {"write", "edit"}:
            return "edit"
        if kind in {"python_exec_agent", "execute", "python", "code"}:
            return "execute"
        return "other"

    def _tool_kind_for_name(self, tool_name: str) -> str:
        lowered = tool_name.strip().lower()
        if lowered == "read":
            return "read"
        if lowered in {"write", "edit"}:
            return "edit"
        if lowered in {"find", "grep"}:
            return "search"
        if lowered == "python_exec_agent":
            return "execute"
        return "other"

    def _next_client_rpc_id(self) -> int:
        self._next_rpc_id += 1
        return self._next_rpc_id

    async def _ensure_task_group(self) -> anyio.abc.TaskGroup:
        if self._task_group is not None:
            return self._task_group
        self._task_group_cm = anyio.create_task_group()
        self._task_group = await self._task_group_cm.__aenter__()
        return self._task_group

    async def _close_task_group(self) -> None:
        if self._task_group_cm is None:
            self._task_group = None
            return
        task_group_cm = self._task_group_cm
        self._task_group_cm = None
        self._task_group = None
        await task_group_cm.__aexit__(None, None, None)

    async def _ensure_session_actor(self, session_id: str) -> _SessionActor:
        actor = self._session_actors.get(session_id)
        if actor is not None:
            return actor
        task_group = await self._ensure_task_group()
        actor = _SessionActor(session_id=session_id, owner=self)
        actor.start(task_group)
        self._session_actors[session_id] = actor
        return actor

    async def _close_all_session_actors(self) -> None:
        actors = list(self._session_actors.values())
        self._session_actors.clear()
        for actor in actors:
            with suppress(Exception):
                await actor.close()

    async def _cancel_session(self, session_id: str) -> None:
        self._cancel_session_state(session_id)
        actor = self._session_actors.get(session_id)
        if actor is not None:
            with suppress(Exception):
                await actor.enqueue_cancel()

    def _cancel_session_state(self, session_id: str) -> None:
        if not session_id:
            return

        self._session_cancelled[session_id] = True

        to_cancel = [
            key
            for key, waiter in self._pending_client_calls.items()
            if waiter.session_id == session_id
        ]
        for key in to_cancel:
            waiter = self._pending_client_calls.pop(key, None)
            if waiter is None:
                continue
            waiter.result = {"outcome": {"outcome": "cancelled"}}
            waiter.event.set()

    def _cancel_all_sessions(self) -> None:
        session_ids = (
            set(self._session_cancelled.keys())
            | set(self._session_actors.keys())
        )
        for session_id in session_ids:
            self._cancel_session_state(session_id)

    async def _emit_session_update(
        self,
        session_id: str,
        update: dict[str, Any],
        *,
        turn_id: str | None = None,
        record_event: bool = True,
    ) -> None:
        payload = {"sessionId": session_id, "update": update}
        if record_event:
            self._persist_session_update_event(payload, turn_id=turn_id)
        await self._send_notification("session/update", payload)

    async def _emit_config_option_update(
        self,
        session_id: str,
        config_options: list[dict[str, Any]],
        *,
        turn_id: str | None = None,
    ) -> None:
        await self._emit_session_update(
            session_id,
            {
                "sessionUpdate": "config_option_update",
                "configOptions": config_options,
            },
            turn_id=turn_id,
        )

    async def _emit_session_info_update_if_changed(
        self,
        session_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        *,
        turn_id: str | None = None,
    ) -> None:
        if not isinstance(after, dict):
            return
        before = before if isinstance(before, dict) else {}

        update: dict[str, Any] = {"sessionUpdate": "session_info_update"}
        changed = False

        before_title = before.get("title")
        after_title = after.get("title")
        if before_title != after_title:
            update["title"] = after_title
            changed = True

        before_updated_at = before.get("updatedAt")
        after_updated_at = after.get("updatedAt")
        if before_updated_at != after_updated_at:
            update["updatedAt"] = after_updated_at
            changed = True

        meta_keys = [
            "messageCount",
            "lastMessageAt",
            "lastMessagePreview",
            "activeProviderId",
            "activeModelId",
            "titleSource",
        ]
        meta_update: dict[str, Any] = {}
        for key in meta_keys:
            if before.get(key) != after.get(key):
                meta_update[key] = after.get(key)
        if meta_update:
            update["_meta"] = meta_update
            changed = True

        if not changed:
            return

        await self._emit_session_update(
            session_id,
            update,
            turn_id=turn_id,
        )

    def _persist_session_update_event(
        self, params: dict[str, Any], *, turn_id: str | None = None
    ) -> None:
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
            turn_id=turn_id,
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

    async def _send_json(self, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.transport.send_json(payload)
