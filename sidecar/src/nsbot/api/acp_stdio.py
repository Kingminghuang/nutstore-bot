from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from acp import RequestError, run_agent
from acp.interfaces import Client

from nsbot.api.acp_app import AcpAppConfig, create_acp_app
from nsbot.api.acp_session import AcpJsonRpcSession


def _acp_debug_enabled() -> bool:
    value = os.environ.get("NSBOT_ACP_DEBUG", "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def _acp_debug_log(message: str) -> None:
    if _acp_debug_enabled():
        print(f"[acp-stdio] {message}", file=sys.stderr, flush=True)


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _json_compatible(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    return value


class _SdkClientTransport:
    def __init__(self) -> None:
        self._client: Client | None = None
        self._session: AcpJsonRpcSession | None = None
        self._pending_request_results: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._request_seq = 0

    def bind_client(self, client: Client) -> None:
        self._client = client

    def bind_session(self, session: AcpJsonRpcSession) -> None:
        self._session = session

    async def accept(self) -> None:
        return

    async def receive_json(self) -> dict[str, Any]:
        raise EOFError("ACP SDK transport does not receive raw JSON messages")

    async def close(self) -> None:
        return

    async def dispatch_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session()
        session.loop = asyncio.get_running_loop()
        request_id = self._next_request_id()
        future = asyncio.get_running_loop().create_future()
        self._pending_request_results[request_id] = future
        await session._handle_request(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        response = await future
        error = response.get("error")
        if isinstance(error, dict):
            raise RequestError(
                int(error.get("code", -32603)),
                str(error.get("message") or "ACP request failed"),
                error.get("data"),
            )
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    async def dispatch_notification(self, method: str, params: dict[str, Any]) -> None:
        session = self._require_session()
        session.loop = asyncio.get_running_loop()
        await session._handle_notification(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )

    async def send_json(self, payload: dict[str, Any]) -> None:
        if "method" in payload and "id" not in payload:
            await self._forward_notification(payload)
            return

        if "method" in payload and "id" in payload:
            response = await self._forward_client_request(payload)
            self._require_session()._handle_client_response(response)
            return

        request_id = str(payload.get("id") or "")
        future = self._pending_request_results.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(payload)

    def _next_request_id(self) -> str:
        self._request_seq += 1
        return f"sdk-{self._request_seq}"

    def _require_client(self) -> Client:
        if self._client is None:
            raise RuntimeError("ACP client connection is unavailable")
        return self._client

    def _require_session(self) -> AcpJsonRpcSession:
        if self._session is None:
            raise RuntimeError("ACP session bridge is unavailable")
        return self._session

    async def _forward_notification(self, payload: dict[str, Any]) -> None:
        client = self._require_client()
        method = str(payload.get("method") or "")
        params = payload.get("params") or {}
        if method == "session/update" and isinstance(params, dict):
            session_id = str(params.get("sessionId") or "")
            update = params.get("update")
            if not session_id or not isinstance(update, dict):
                raise RuntimeError("session/update payload is invalid")
            await client.session_update(session_id=session_id, update=update)
            return
        _acp_debug_log(f"ignoring unsupported outbound notification method={method}")

    async def _forward_client_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._require_client()
        method = str(payload.get("method") or "")
        params = payload.get("params") or {}
        if method != "session/request_permission" or not isinstance(params, dict):
            raise RuntimeError(f"Unsupported outbound ACP client request: {method}")

        result = await client.request_permission(
            options=params.get("options") or [],
            session_id=str(params.get("sessionId") or ""),
            tool_call=params.get("toolCall") or {},
        )
        serialized_result = (
            result.model_dump(by_alias=True, exclude_none=True)
            if hasattr(result, "model_dump")
            else result
        )
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "result": serialized_result if isinstance(serialized_result, dict) else {},
        }


class _AcpSdkAgent:
    def __init__(self, app_state: Any):
        self._transport = _SdkClientTransport()
        self._session = AcpJsonRpcSession(self._transport, app_state)
        self._transport.bind_session(self._session)

    def on_connect(self, conn: Client) -> None:
        self._transport.bind_client(conn)

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: dict[str, Any] | None = None,
        client_info: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        return await self._transport.dispatch_request(
            "initialize",
            _compact(
                {
                    "protocolVersion": protocol_version,
                    "clientCapabilities": client_capabilities,
                    "clientInfo": client_info,
                }
            ),
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return await self._transport.dispatch_request(
            "authenticate",
            {"methodId": method_id},
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        return await self._transport.dispatch_request(
            "session/new",
            _compact({"cwd": cwd, "mcpServers": mcp_servers}),
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        return await self._transport.dispatch_request(
            "session/load",
            _compact(
                {
                    "cwd": cwd,
                    "sessionId": session_id,
                    "mcpServers": mcp_servers,
                }
            ),
        )

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        return await self._transport.dispatch_request(
            "session/list",
            _compact({"cursor": cursor, "cwd": cwd}),
        )

    async def set_session_mode(
        self,
        mode_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        return await self._transport.dispatch_request(
            "session/set_mode",
            {"sessionId": session_id, "modeId": mode_id},
        )

    async def set_session_model(
        self,
        model_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        await self._transport.dispatch_request(
            "session/set_config_option",
            {"sessionId": session_id, "configId": "model", "value": model_id},
        )
        return {}

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        return await self._transport.dispatch_request(
            "session/set_config_option",
            {"sessionId": session_id, "configId": config_id, "value": value},
        )

    async def prompt(
        self,
        prompt: list[dict[str, Any]],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        meta = kwargs or None
        return await self._transport.dispatch_request(
            "session/prompt",
            _compact(
                {
                    "sessionId": session_id,
                    "prompt": _json_compatible(prompt),
                    "messageId": message_id,
                    "_meta": meta,
                }
            ),
        )

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del cwd, session_id, mcp_servers, kwargs
        raise RequestError.method_not_found("session/fork")

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        return await self._transport.dispatch_request(
            "session/resume",
            _compact(
                {
                    "cwd": cwd,
                    "sessionId": session_id,
                    "mcpServers": mcp_servers,
                }
            ),
        )

    async def close_session(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
        del session_id, kwargs
        raise RequestError.method_not_found("session/close")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        del kwargs
        await self._transport.dispatch_notification(
            "session/cancel",
            {"sessionId": session_id},
        )

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._transport.dispatch_request(f"_{method}", params)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        await self._transport.dispatch_notification(f"_{method}", params)


def _config_from_env() -> AcpAppConfig:
    ns_bot_home_value = os.environ.get("NS_BOT_HOME")
    return AcpAppConfig(
        ns_bot_home=ns_bot_home_value,
        fd_executable=os.environ.get("NSBOT_FD_EXECUTABLE") or None,
        rg_executable=os.environ.get("NSBOT_RG_EXECUTABLE") or None,
    )


async def run_stdio(config: AcpAppConfig) -> None:
    app = create_acp_app(config)
    _acp_debug_log("starting ACP SDK stdio session")
    await run_agent(_AcpSdkAgent(app.state))


def main(config: AcpAppConfig | None = None) -> int:
    asyncio.run(run_stdio(config or _config_from_env()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
