from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from nsbot_sidecar.api.api_server import ApiServerConfig, create_app
from nsbot_sidecar.api.acp_session import AcpJsonRpcSession


class _InMemoryTransport:
    def __init__(self, incoming: list[dict[str, Any]]):
        self._incoming = list(incoming)
        self.outgoing: list[dict[str, Any]] = []
        self.closed = False

    async def accept(self) -> None:
        return

    async def receive_json(self) -> dict[str, Any]:
        if not self._incoming:
            raise EOFError("done")
        return self._incoming.pop(0)

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.outgoing.append(payload)

    async def close(self) -> None:
        self.closed = True


def _response_for(outgoing: list[dict[str, Any]], request_id: int) -> dict[str, Any]:
    for payload in outgoing:
        if payload.get("id") == request_id:
            return payload
    raise AssertionError(f"missing response for id {request_id}")


class ApiServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="sidecar-api-"))
        self.config = ApiServerConfig(
            host="127.0.0.1",
            port=18765,
            auth_header_value="Bearer test-token",
            ns_bot_home=str(self.temp_dir),
        )
        self.app = create_app(self.config)
        self.client = TestClient(self.app)

    def test_health_endpoint_is_available(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)

    def test_acp_ws_route_is_not_exposed(self) -> None:
        response = self.client.get("/acp/ws")
        self.assertEqual(response.status_code, 404)

    def test_acp_session_initialize_and_workspace_crud(self) -> None:
        workspace_path = self.temp_dir / "workspace-a"
        workspace_path.mkdir(parents=True, exist_ok=True)
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
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "workspace/create",
                    "params": {
                        "name": "workspace-a",
                        "realPath": str(workspace_path),
                        "pathLabel": str(workspace_path),
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "workspace/list",
                    "params": {},
                },
            ]
        )

        asyncio.run(AcpJsonRpcSession(transport, self.app.state).run())

        init_resp = _response_for(transport.outgoing, 1)
        self.assertEqual(init_resp["result"]["protocolVersion"], 1)
        create_resp = _response_for(transport.outgoing, 2)
        self.assertIn("id", create_resp["result"])
        list_resp = _response_for(transport.outgoing, 3)
        self.assertEqual(len(list_resp["result"]["workspaces"]), 1)

    def test_acp_session_provider_and_session_create(self) -> None:
        workspace_path = self.temp_dir / "workspace-b"
        workspace_path.mkdir(parents=True, exist_ok=True)
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
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "workspace/create",
                    "params": {
                        "name": "workspace-b",
                        "realPath": str(workspace_path),
                        "pathLabel": str(workspace_path),
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "provider/create",
                    "params": {
                        "kind": "builtin",
                        "catalogProviderId": "openai",
                        "displayName": "OpenAI",
                        "apiKey": "sk-test",
                        "preferredModelId": "gpt-5.4",
                    },
                },
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app.state).run())
        workspace_id = _response_for(transport.outgoing, 2)["result"]["id"]
        provider_id = _response_for(transport.outgoing, 3)["result"]["id"]

        create_transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "workspace/sessions/create",
                    "params": {
                        "workspaceId": workspace_id,
                        "connectionId": provider_id,
                        "modelId": "gpt-5.4",
                    },
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(create_transport, self.app.state).run())
        create_session_resp = _response_for(create_transport.outgoing, 4)
        session_id = create_session_resp["result"]["id"]
        self.assertTrue(session_id)

        load_transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "session/load",
                    "params": {"sessionId": session_id},
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(load_transport, self.app.state).run())
        load_resp = _response_for(load_transport.outgoing, 5)
        self.assertIn("configOptions", load_resp["result"])

    def test_acp_session_timeline_list_returns_events(self) -> None:
        workspace_path = self.temp_dir / "workspace-c"
        workspace_path.mkdir(parents=True, exist_ok=True)
        repos = self.app.state.repositories
        workspace = repos.workspaces.create(
            name="workspace-c",
            path_label=str(workspace_path),
            real_path=str(workspace_path),
        )
        provider = repos.providers.save_bundle(
            connection_data={
                "kind": "builtin",
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "api_key_configured": True,
                "preferred_model_id": "gpt-5.4",
            }
        )
        session = repos.sessions.create(
            workspace_id=workspace.id,
            active_connection_id=provider.connection.id,
            active_model_id="gpt-5.4",
        )
        repos.acp_event_log.append(
            session_id=session.id,
            event_type="user_message_chunk",
            event_json='{"method":"session/update","params":{"sessionId":"x","update":{"sessionUpdate":"user_message_chunk","content":{"type":"text","text":"hello"}}}}',
        )

        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "timeline/list",
                    "params": {"sessionId": session.id, "limit": 20},
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app.state).run())
        timeline_resp = _response_for(transport.outgoing, 1)
        self.assertEqual(len(timeline_resp["result"]["events"]), 1)


if __name__ == "__main__":
    unittest.main()
