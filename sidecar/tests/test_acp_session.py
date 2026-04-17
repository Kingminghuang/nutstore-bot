from __future__ import annotations

import asyncio
import json
import threading
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
import subprocess

from nsbot_sidecar.api.acp_session import AcpJsonRpcSession
from nsbot_sidecar.api.api_server import ApiServerConfig, create_app
from nsbot_sidecar.infrastructure.secret_store import ProviderSecretPayload
from nsbot_sidecar.runtime.runtime_service import RuntimeCancelledError


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


class _BlockingCancellableEngine:
    def __init__(self) -> None:
        self.started = threading.Event()

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
        del turn_id, user_input, auth_context, metadata, permission_requester
        self.started.set()
        if event_callback is not None:
            event_callback({"type": "delta", "payload": {"text": "working"}})
        while is_cancelled is not None and not is_cancelled():
            time.sleep(0.01)
        raise RuntimeCancelledError()


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
            provider_data={
                "kind": "builtin",
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "api_key_configured": True,
                "preferred_model_id": "gpt-5.4",
            }
        )
        self.app_state.secret_store.save_provider_secret(
            self.provider.provider.secret_ref,
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
            active_provider_id=self.provider.provider.id,
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
                    "method": "_nsbot/timeline/list",
                    "params": {"sessionId": session.id},
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        result = _response_for(transport.outgoing, 2)["result"]
        self.assertEqual(len(result["events"]), 1)

    def test_prefixed_extension_method_is_accepted(self) -> None:
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 21,
                    "method": "_nsbot/provider/catalog",
                    "params": {},
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        response = _response_for(transport.outgoing, 21)
        self.assertIn("result", response)
        self.assertIn("providers", response["result"])

    def test_unprefixed_extension_method_is_rejected(self) -> None:
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 98,
                    "method": "nsbot/provider/catalog",
                    "params": {},
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        response = _response_for(transport.outgoing, 98)
        self.assertEqual(response["error"]["code"], -32601)

    def test_legacy_extension_method_is_rejected(self) -> None:
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 99,
                    "method": "timeline/list",
                    "params": {"sessionId": "missing"},
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        response = _response_for(transport.outgoing, 99)
        self.assertEqual(response["error"]["code"], -32601)

    def test_resource_link_without_name_is_auto_filled(self) -> None:
        transport = _InMemoryTransport([])
        session_runner = AcpJsonRpcSession(transport, self.app_state)

        async def _invoke() -> tuple[str, list[dict[str, Any]]]:
            return await session_runner._extract_prompt_text(
                "sess_1",
                [
                    {
                        "type": "resource_link",
                        "uri": "file:///tmp/notes.md",
                    }
                ],
            )

        prompt_text, normalized = asyncio.run(_invoke())
        self.assertEqual(
            prompt_text,
            "Referenced workspace entry [/tmp/notes.md](/tmp/notes.md). The agent can inspect this path directly if needed.",
        )
        self.assertEqual(normalized[0]["name"], "notes.md")

    def test_resource_link_preserves_metadata_fields(self) -> None:
        transport = _InMemoryTransport([])
        session_runner = AcpJsonRpcSession(transport, self.app_state)

        async def _invoke() -> tuple[str, list[dict[str, Any]]]:
            return await session_runner._extract_prompt_text(
                "sess_1",
                [
                    {
                        "type": "resource_link",
                        "uri": "file:///tmp/report.pdf",
                        "name": "report.pdf",
                        "mimeType": "application/pdf",
                        "title": "Quarterly Report",
                        "description": "Q1 results",
                        "size": "12345",
                    }
                ],
            )

        prompt_text, normalized = asyncio.run(_invoke())
        self.assertIn("[/tmp/report.pdf](/tmp/report.pdf)", prompt_text)
        self.assertIn("Display label: Quarterly Report.", prompt_text)
        self.assertIn("Q1 results.", prompt_text)
        self.assertIn("MIME type: application/pdf.", prompt_text)
        self.assertIn("Size: 12345 bytes.", prompt_text)
        self.assertEqual(normalized[0]["mimeType"], "application/pdf")
        self.assertEqual(normalized[0]["title"], "Quarterly Report")
        self.assertEqual(normalized[0]["description"], "Q1 results")
        self.assertEqual(normalized[0]["size"], 12345)

    def test_resource_link_non_file_uri_uses_uri_projection(self) -> None:
        transport = _InMemoryTransport([])
        session_runner = AcpJsonRpcSession(transport, self.app_state)

        async def _invoke() -> tuple[str, list[dict[str, Any]]]:
            return await session_runner._extract_prompt_text(
                "sess_1",
                [
                    {
                        "type": "resource_link",
                        "uri": "https://example.com/spec",
                        "name": "spec",
                        "description": "External reference",
                    }
                ],
            )

        prompt_text, normalized = asyncio.run(_invoke())
        self.assertEqual(
            prompt_text,
            "Referenced resource spec at https://example.com/spec. External reference.",
        )
        self.assertEqual(normalized[0]["name"], "spec")

    def test_workspace_find_entries_returns_fd_matches(self) -> None:
        workspace_root = Path(self.workspace.real_path)
        (workspace_root / "src" / "app").mkdir(parents=True, exist_ok=True)
        (workspace_root / "src" / "app" / "page.tsx").write_text("export default null\n")
        (workspace_root / "src" / "components").mkdir(parents=True, exist_ok=True)
        (workspace_root / "pa").write_text("exact basename\n")
        (workspace_root / "package.json").write_text("{}\n")
        (workspace_root / "pages").mkdir(parents=True, exist_ok=True)
        (workspace_root / "src" / "pa-tools").mkdir(parents=True, exist_ok=True)
        (workspace_root / "src" / "app" / "shape.ts").write_text("export const shape = true\n")

        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 20,
                    "method": "_nsbot/workspace/find_entries",
                    "params": {"workspaceId": self.workspace.id, "query": "pa", "limit": 5},
                }
            ]
        )

        with patch("nsbot_sidecar.api.acp_session.shutil.which", return_value="/usr/bin/fd"), patch(
            "nsbot_sidecar.api.acp_session.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["/usr/bin/fd"],
                returncode=0,
                stdout="src/app/page.tsx\nsrc/app\npa\npackage.json\npages\nsrc/pa-tools\nsrc/app/shape.ts\n",
                stderr="",
            ),
        ):
            asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())

        result = _response_for(transport.outgoing, 20)["result"]
        self.assertEqual(len(result["entries"]), 5)
        self.assertEqual(
            [entry["relativePath"] for entry in result["entries"]],
            [
                "pa",
                "package.json",
                "src/app/page.tsx",
                "pages/",
                "src/pa-tools/",
            ],
        )
        self.assertEqual(result["entries"][0]["entryType"], "file")
        self.assertEqual(result["entries"][1]["entryType"], "file")
        self.assertEqual(result["entries"][2]["parentPath"], "src/app")
        self.assertEqual(result["entries"][2]["entryType"], "file")
        self.assertEqual(result["entries"][3]["entryType"], "directory")
        self.assertEqual(result["entries"][4]["entryType"], "directory")

    def test_attachment_resource_is_expanded_into_prompt_text(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        attachment = self.app_state.session_service.create_attachment(
            session.id,
            file_name="notes.txt",
            mime_type="text/plain",
            payload=b"hello from attachment",
        )
        transport = _InMemoryTransport([])
        session_runner = AcpJsonRpcSession(transport, self.app_state)

        async def _invoke() -> tuple[str, list[dict[str, Any]]]:
            return await session_runner._extract_prompt_text(
                session.id,
                [
                    {
                        "type": "resource",
                        "resource": {
                            "uri": f"attachment://session/{attachment['id']}",
                            "mimeType": "text/plain",
                        },
                    }
                ],
            )

        prompt_text, normalized = asyncio.run(_invoke())
        self.assertIn("Attached file notes.txt", prompt_text)
        self.assertIn("hello from attachment", prompt_text)
        self.assertEqual(normalized[0]["resource"]["text"], "hello from attachment")
        self.assertEqual(normalized[0]["resource"]["title"], "notes.txt")

    def test_user_message_chunk_uses_display_text_for_attachment_resources(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        attachment = self.app_state.session_service.create_attachment(
            session.id,
            file_name="notes.txt",
            mime_type="text/plain",
            payload=b"hello from attachment",
        )
        transport = _InMemoryTransport([])
        session_runner = AcpJsonRpcSession(transport, self.app_state)

        async def _invoke() -> None:
            session_runner.loop = asyncio.get_running_loop()
            await session_runner._handle_prompt_request(
                7,
                {
                    "sessionId": session.id,
                    "prompt": [
                        {"type": "text", "text": "Summarize this"},
                        {
                            "type": "resource",
                            "resource": {
                                "uri": f"attachment://session/{attachment['id']}",
                                "mimeType": "text/plain",
                                "title": "notes.txt",
                            },
                        },
                    ],
                },
            )

        with patch("nsbot_sidecar.api.acp_session.create_runtime_engine", return_value=_FakeEngine()):
            asyncio.run(_invoke())

        update_payloads = [
            payload
            for payload in transport.outgoing
            if payload.get("method") == "session/update"
        ]
        user_update = next(
            payload
            for payload in update_payloads
            if payload["params"]["update"].get("sessionUpdate") == "user_message_chunk"
        )
        content = user_update["params"]["update"]["content"]
        self.assertEqual(content["displayText"], "Summarize this\nnotes.txt")
        self.assertEqual(content["editableText"], "Summarize this")
        self.assertEqual(content["promptBlocks"][1]["resource"]["title"], "notes.txt")

    def test_prompt(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
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

    def test_prompt_can_be_cancelled(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport([])
        engine = _BlockingCancellableEngine()

        with patch(
            "nsbot_sidecar.api.acp_session.create_runtime_engine",
            return_value=engine,
        ):

            async def _invoke_prompt_then_cancel() -> None:
                session_runner = AcpJsonRpcSession(transport, self.app_state)
                prompt_task = asyncio.create_task(
                    session_runner._handle_prompt_request(
                        6,
                        {
                            "sessionId": session.id,
                            "prompt": [{"type": "text", "text": "cancel me"}],
                        },
                    )
                )
                await asyncio.to_thread(engine.started.wait, 1)
                await session_runner._handle_notification(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/cancel",
                        "params": {"sessionId": session.id},
                    }
                )
                await prompt_task

            asyncio.run(_invoke_prompt_then_cancel())

        response = _response_for(transport.outgoing, 6)
        self.assertEqual(response["result"]["stopReason"], "cancelled")
        self.assertTrue(
            any(
                payload.get("method") == "session/update"
                and payload.get("params", {}).get("update", {}).get("sessionUpdate")
                == "user_message_chunk"
                for payload in transport.outgoing
            )
        )

    def test_edit_and_prompt_rejects_non_latest_user_event(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        self.app_state.repositories.acp_event_log.append(
            session_id=session.id,
            event_id="evt_user_1",
            sequence_no=1,
            event_type="user_message_chunk",
            event_json=self._session_update_event_json(
                session.id,
                {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "first"}},
            ),
        )
        self.app_state.repositories.acp_event_log.append(
            session_id=session.id,
            event_id="evt_user_2",
            sequence_no=2,
            event_type="user_message_chunk",
            event_json=self._session_update_event_json(
                session.id,
                {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "second"}},
            ),
        )

        transport = _InMemoryTransport([])
        session_runner = AcpJsonRpcSession(transport, self.app_state)

        async def _invoke() -> None:
            await session_runner._handle_edit_and_prompt_request(
                4,
                {
                    "sessionId": session.id,
                    "eventId": "evt_user_1",
                    "prompt": [{"type": "text", "text": "edited"}],
                },
            )

        asyncio.run(_invoke())
        response = _response_for(transport.outgoing, 4)
        self.assertEqual(
            response["error"]["message"],
            "Only the latest user input event can be edited",
        )

    def test_edit_and_prompt_allows_latest_user_event(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        self.app_state.repositories.acp_event_log.append(
            session_id=session.id,
            event_id="evt_user_1",
            sequence_no=1,
            event_type="user_message_chunk",
            event_json=self._session_update_event_json(
                session.id,
                {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "first"}},
            ),
        )
        self.app_state.repositories.acp_event_log.append(
            session_id=session.id,
            event_id="evt_user_2",
            sequence_no=2,
            event_type="user_message_chunk",
            event_json=self._session_update_event_json(
                session.id,
                {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "second"}},
            ),
        )

        transport = _InMemoryTransport([])
        with patch("nsbot_sidecar.api.acp_session.create_runtime_engine", return_value=_FakeEngine()):
            async def _invoke() -> None:
                session_runner = AcpJsonRpcSession(transport, self.app_state)
                await session_runner._handle_edit_and_prompt_request(
                    5,
                    {
                        "sessionId": session.id,
                        "eventId": "evt_user_2",
                        "prompt": [{"type": "text", "text": "edited"}],
                    },
                )

            asyncio.run(_invoke())
        response = _response_for(transport.outgoing, 5)
        self.assertEqual(response["result"]["stopReason"], "end_turn")

    def test_edit_and_prompt_allows_latest_user_when_last_event_is_assistant(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        self.app_state.repositories.acp_event_log.append(
            session_id=session.id,
            event_id="evt_user_1",
            sequence_no=1,
            event_type="user_message_chunk",
            event_json=self._session_update_event_json(
                session.id,
                {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "question"}},
            ),
        )
        self.app_state.repositories.acp_event_log.append(
            session_id=session.id,
            event_id="evt_assistant_1",
            sequence_no=2,
            event_type="agent_message_chunk",
            event_json=self._session_update_event_json(
                session.id,
                {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "answer"}},
            ),
        )

        transport = _InMemoryTransport([])
        with patch("nsbot_sidecar.api.acp_session.create_runtime_engine", return_value=_FakeEngine()):
            async def _invoke() -> None:
                session_runner = AcpJsonRpcSession(transport, self.app_state)
                await session_runner._handle_edit_and_prompt_request(
                    6,
                    {
                        "sessionId": session.id,
                        "eventId": "evt_user_1",
                        "prompt": [{"type": "text", "text": "edited question"}],
                    },
                )

            asyncio.run(_invoke())
        response = _response_for(transport.outgoing, 6)
        self.assertEqual(response["result"]["stopReason"], "end_turn")

    def _session_update_event_json(self, session_id: str, update: dict[str, Any]) -> str:
        return json.dumps(
            {
                "method": "session/update",
                "params": {"sessionId": session_id, "update": update},
            }
        )


if __name__ == "__main__":
    unittest.main()
