from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from nsbot_sidecar.api.acp_session import AcpJsonRpcSession
from nsbot_sidecar.api.api_server import ApiServerConfig, create_app
from nsbot_sidecar.infrastructure.secret_store import ProviderSecretPayload


class _InMemoryTransport:
    def __init__(self, incoming: list[dict[str, Any]]):
        self._incoming = list(incoming)
        self.outgoing: list[dict[str, Any]] = []

    async def accept(self) -> None:
        return

    async def receive_json(self) -> dict[str, Any]:
        if not self._incoming:
            raise EOFError("done")
        return self._incoming.pop(0)

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.outgoing.append(payload)

    async def close(self) -> None:
        return


def _response_for(outgoing: list[dict[str, Any]], request_id: int) -> dict[str, Any]:
    for payload in outgoing:
        if payload.get("id") == request_id:
            return payload
    raise AssertionError(f"missing response for id {request_id}")


class _FakeEngine:
    def process(
        self,
        turn_id,
        user_input,
        auth_context,
        metadata,
        event_callback=None,
        is_cancelled=None,
        permission_requester=None,
    ):
        del turn_id, auth_context, metadata, is_cancelled, permission_requester
        if event_callback is not None:
            event_callback({"type": "delta", "payload": {"text": "chunk"}})
        return {"final_answer": f"ok: {user_input}"}


class AcpSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="acp-session-"))
        app = create_app(
            ApiServerConfig(
                host="127.0.0.1",
                port=18765,
                auth_header_value="Bearer test-token",
                ns_bot_home=str(temp_dir),
            )
        )
        self.app_state = app.state
        self.workspace = self.app_state.repositories.workspaces.create(
            name="ws",
            path_label=str(temp_dir / "ws"),
            real_path=str(temp_dir / "ws"),
        )
        self.provider = self.app_state.repositories.providers.save_bundle(
            connection_data={
                "kind": "builtin",
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "api_key_configured": True,
                "preferred_model_id": "gpt-5.4",
            }
        )
        self.app_state.secret_store.save_provider_secret(
            self.provider.connection.secret_ref,
            ProviderSecretPayload(version=1, api_key="sk-test"),
        )

    def test_initialize(self) -> None:
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": 1,
                        "clientCapabilities": {
                            "fs": {"readTextFile": False, "writeTextFile": False},
                            "terminal": False,
                        },
                    },
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        self.assertEqual(_response_for(transport.outgoing, 1)["result"]["protocolVersion"], 1)

    def test_timeline_list(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_connection_id=self.provider.connection.id,
            active_model_id="gpt-5.4",
        )
        self.app_state.repositories.acp_event_log.append(
            session_id=session.id,
            event_type="user_message_chunk",
            event_json='{"method":"session/update","params":{"sessionId":"x","update":{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"hello"}}}}',
        )
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "timeline/list",
                    "params": {"sessionId": session.id},
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        result = _response_for(transport.outgoing, 2)["result"]
        self.assertEqual(len(result["events"]), 1)

    def test_prompt(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_connection_id=self.provider.connection.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport([])
        with patch("nsbot_sidecar.api.acp_session.create_runtime_engine", return_value=_FakeEngine()):
            async def _invoke_prompt() -> None:
                session_runner = AcpJsonRpcSession(transport, self.app_state)
                await session_runner._handle_prompt_request(
                    3,
                    {
                        "sessionId": session.id,
                        "prompt": [{"type": "text", "text": "ping"}],
                    },
                )

            asyncio.run(_invoke_prompt())
        response = _response_for(transport.outgoing, 3)
        self.assertEqual(response["result"]["stopReason"], "end_turn")


if __name__ == "__main__":
    unittest.main()
