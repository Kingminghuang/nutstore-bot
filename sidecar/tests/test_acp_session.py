from __future__ import annotations

import asyncio
import json
import os
import threading
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
import subprocess

import anyio
from nsbot_sidecar.api.acp_app import AcpAppConfig, create_acp_app
from nsbot_sidecar.api.acp_session import AcpJsonRpcSession
from nsbot_sidecar.infrastructure.secret_store import ProviderSecretPayload
from nsbot_sidecar.runtime.types import RuntimeCancelledError, RuntimeProcessError


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
    async def process_async(
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
        final_text = f"ok: {user_input}"
        if event_callback is not None:
            await anyio.to_thread.run_sync(
                event_callback, {"type": "delta", "payload": {"text": final_text}}
            )
        return {"final_answer": final_text}


class _BlockingCancellableEngine:
    def __init__(self) -> None:
        self.started = threading.Event()

    async def process_async(
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
            await anyio.to_thread.run_sync(
                event_callback, {"type": "delta", "payload": {"text": "working"}}
            )
        while is_cancelled is not None and not is_cancelled():
            await asyncio.sleep(0.01)
        raise RuntimeCancelledError()


class _FinalOnlyEngine:
    async def process_async(
        self,
        turn_id,
        user_input,
        auth_context,
        metadata,
        event_callback=None,
        is_cancelled=None,
        permission_requester=None,
    ):
        del turn_id, auth_context, metadata, event_callback, is_cancelled, permission_requester
        return {"final_answer": f"ok: {user_input}"}


class _ErrorEngine:
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message

    async def process_async(
        self,
        turn_id,
        user_input,
        auth_context,
        metadata,
        event_callback=None,
        is_cancelled=None,
        permission_requester=None,
    ):
        del turn_id, user_input, auth_context, metadata, event_callback, is_cancelled, permission_requester
        raise RuntimeProcessError(self.code, self.message)


class AcpSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="acp-session-"))
        app = create_acp_app(AcpAppConfig(ns_bot_home=str(temp_dir)))
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
        result = _response_for(transport.outgoing, 1)["result"]
        self.assertEqual(result["protocolVersion"], 1)
        self.assertEqual(result["agentCapabilities"]["mcpCapabilities"]["http"], True)
        self.assertEqual(result["agentCapabilities"]["mcpCapabilities"]["sse"], False)
        method_ids = {str(method.get("id") or "") for method in result["authMethods"]}
        self.assertIn("USE_OPENAI", method_ids)
        self.assertIn("USE_ANTHROPIC", method_ids)
        self.assertIn("USE_GEMINI", method_ids)
        self.assertIn("USE_DEEPSEEK", method_ids)
        self.assertIn("GATEWAY", method_ids)

    def test_authenticate_with_meta_api_key_updates_provider_secret(self) -> None:
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "authenticate",
                    "params": {
                        "methodId": "USE_OPENAI",
                        "_meta": {"api-key": "sk-meta-openai"},
                    },
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        result = _response_for(transport.outgoing, 1)["result"]
        self.assertEqual(result["_meta"]["auth"]["keySource"], "meta")
        self.assertEqual(result["_meta"]["auth"]["targetProviderId"], "openai")
        bundle = self.app_state.repositories.providers.get_bundle_by_id_or_raise("openai")
        secret = self.app_state.secret_store.load_provider_secret(bundle.provider.secret_ref)
        self.assertIsNotNone(secret)
        self.assertEqual(secret.api_key, "sk-meta-openai")

    def test_authenticate_falls_back_to_default_provider_secret(self) -> None:
        self.app_state.provider_service.set_default_model(
            self.provider.provider.id, "gpt-5.4"
        )
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "authenticate",
                    "params": {
                        "methodId": "USE_ANTHROPIC",
                    },
                }
            ]
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        result = _response_for(transport.outgoing, 1)["result"]
        self.assertEqual(result["_meta"]["auth"]["targetProviderId"], "anthropic")
        self.assertEqual(
            result["_meta"]["auth"]["effectiveProviderId"], self.provider.provider.id
        )
        self.assertEqual(result["_meta"]["auth"]["keySource"], "default_provider_secret")

    def test_authenticate_rejects_unknown_method(self) -> None:
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "authenticate",
                    "params": {"methodId": "NOT_SUPPORTED"},
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        response = _response_for(transport.outgoing, 1)
        self.assertEqual(response["error"]["code"], -32000)
        self.assertIn("unsupported authentication method", response["error"]["message"])

    def test_session_new_requires_authenticate(self) -> None:
        workspace_path = self.workspace.real_path
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session/new",
                    "params": {"cwd": workspace_path, "mcpServers": []},
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        response = _response_for(transport.outgoing, 1)
        self.assertEqual(response["error"]["code"], -32000)
        self.assertIn("Authentication required", response["error"]["message"])

    def test_normalize_mcp_http_server_parameter(self) -> None:
        session_runner = AcpJsonRpcSession(_InMemoryTransport([]), self.app_state)
        normalized = session_runner._normalize_mcp_server_parameter(
            {
                "type": "http",
                "name": "demo-http",
                "url": "https://example.com/mcp",
                "headers": [
                    {"name": "Authorization", "value": "Bearer token"},
                    {"name": "X-Tenant", "value": "abc"},
                ],
            }
        )
        self.assertEqual(normalized["transport"], "streamable-http")
        self.assertEqual(normalized["url"], "https://example.com/mcp")
        self.assertEqual(normalized["headers"]["Authorization"], "Bearer token")
        self.assertEqual(normalized["headers"]["X-Tenant"], "abc")

    def test_normalize_mcp_sse_server_parameter_rejected(self) -> None:
        session_runner = AcpJsonRpcSession(_InMemoryTransport([]), self.app_state)
        with self.assertRaisesRegex(RuntimeError, "not supported"):
            session_runner._normalize_mcp_server_parameter(
                {"type": "sse", "name": "legacy-sse", "url": "https://example.com/sse"}
            )

    def test_tool_kind_mapping_matches_acp_enum(self) -> None:
        session_runner = AcpJsonRpcSession(_InMemoryTransport([]), self.app_state)
        self.assertEqual(session_runner._tool_kind_for_name("read"), "read")
        self.assertEqual(session_runner._tool_kind_for_name("edit"), "edit")
        self.assertEqual(session_runner._tool_kind_for_name("write"), "edit")
        self.assertEqual(session_runner._tool_kind_for_name("find"), "search")
        self.assertEqual(session_runner._tool_kind_for_name("grep"), "search")
        self.assertEqual(
            session_runner._tool_kind_for_name("python_exec_agent"), "execute"
        )
        self.assertEqual(session_runner._tool_kind_for_name("unknown_tool"), "other")
        self.assertEqual(
            session_runner._permission_kind_for_request({"kind": "write"}), "edit"
        )
        self.assertEqual(
            session_runner._permission_kind_for_request({"kind": "edit"}), "edit"
        )
        self.assertEqual(
            session_runner._permission_kind_for_request(
                {"kind": "python_exec_agent"}
            ),
            "execute",
        )
        self.assertEqual(
            session_runner._permission_kind_for_request({"kind": "code"}),
            "execute",
        )

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

    def test_session_load_replays_history_before_response(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        self.app_state.repositories.acp_event_log.append(
            session_id=session.id,
            event_type="user_message_chunk",
            event_json=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session.id,
                        "update": {
                            "sessionUpdate": "user_message_chunk",
                            "content": {"type": "text", "text": "hello"},
                        },
                    },
                }
            ),
        )
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "authenticate",
                    "params": {"methodId": "USE_OPENAI"},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session/load",
                    "params": {"sessionId": session.id, "mcpServers": []},
                },
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        load_response_index = next(
            index
            for index, payload in enumerate(transport.outgoing)
            if payload.get("id") == 2
        )
        replay_index = next(
            index
            for index, payload in enumerate(transport.outgoing)
            if payload.get("method") == "session/update"
            and payload.get("params", {}).get("sessionId") == session.id
        )
        self.assertLess(replay_index, load_response_index)

    def test_session_list_paginates_with_cursor(self) -> None:
        workspace_path = Path(self.workspace.real_path).resolve()
        workspace_path.mkdir(parents=True, exist_ok=True)
        self.app_state.repositories.workspaces.update(
            self.workspace.id,
            real_path=str(workspace_path),
        )
        for index in range(101):
            session = self.app_state.repositories.sessions.create(
                workspace_id=self.workspace.id,
                active_provider_id=self.provider.provider.id,
                active_model_id="gpt-5.4",
            )
            self.app_state.repositories.sessions.touch(
                session.id,
                updated_at=f"2026-04-20T00:{index // 60:02d}:{index % 60:02d}Z",
            )

        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session/list",
                    "params": {"cwd": str(workspace_path)},
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        first_page = _response_for(transport.outgoing, 1)["result"]
        self.assertEqual(len(first_page["sessions"]), 100)
        self.assertIn("nextCursor", first_page)

        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session/list",
                    "params": {
                        "cwd": str(workspace_path),
                        "cursor": first_page["nextCursor"],
                    },
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        second_page = _response_for(transport.outgoing, 2)["result"]
        self.assertEqual(len(second_page["sessions"]), 1)
        self.assertNotIn("nextCursor", second_page)

    def test_thought_level_persists_across_session_load(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session/set_config_option",
                    "params": {
                        "sessionId": session.id,
                        "configId": "thought_level",
                        "value": "high",
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "authenticate",
                    "params": {"methodId": "USE_OPENAI"},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session/load",
                    "params": {"sessionId": session.id, "mcpServers": []},
                },
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        config_options = _response_for(transport.outgoing, 3)["result"]["configOptions"]
        thought_option = next(
            item for item in config_options if item.get("id") == "thought_level"
        )
        self.assertEqual(thought_option["currentValue"], "high")

    def test_set_config_option_emits_config_option_update(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "session/set_config_option",
                    "params": {
                        "sessionId": session.id,
                        "configId": "thought_level",
                        "value": "high",
                    },
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        response = _response_for(transport.outgoing, 1)
        self.assertIn("result", response)
        notification = next(
            payload
            for payload in transport.outgoing
            if payload.get("method") == "session/update"
            and payload.get("params", {}).get("update", {}).get("sessionUpdate")
            == "config_option_update"
        )
        thought_option = next(
            item
            for item in notification["params"]["update"]["configOptions"]
            if item.get("id") == "thought_level"
        )
        self.assertEqual(thought_option["currentValue"], "high")

    def test_update_meta_emits_session_info_update(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "_nsbot/session/update_meta",
                    "params": {
                        "sessionId": session.id,
                        "title": "Renamed Session",
                    },
                }
            ]
        )
        asyncio.run(AcpJsonRpcSession(transport, self.app_state).run())
        response = _response_for(transport.outgoing, 1)
        self.assertEqual(response["result"]["title"], "Renamed Session")
        notification = next(
            payload
            for payload in transport.outgoing
            if payload.get("method") == "session/update"
            and payload.get("params", {}).get("update", {}).get("sessionUpdate")
            == "session_info_update"
        )
        self.assertEqual(notification["params"]["update"]["title"], "Renamed Session")

    def test_runtime_planning_emits_full_plan_state(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport([])
        session_runner = AcpJsonRpcSession(transport, self.app_state)

        async def _invoke() -> None:
            await session_runner._handle_runtime_event(
                session.id,
                "turn-1",
                {
                    "type": "timeline_entry",
                    "payload": {
                        "entry_kind": "planning",
                        "content_text": "Step one",
                    },
                },
            )
            await session_runner._handle_runtime_event(
                session.id,
                "turn-1",
                {
                    "type": "timeline_entry",
                    "payload": {
                        "entry_kind": "planning",
                        "content_text": "Step two",
                    },
                },
            )

        asyncio.run(_invoke())
        plan_updates = [
            payload["params"]["update"]
            for payload in transport.outgoing
            if payload.get("method") == "session/update"
            and payload.get("params", {}).get("update", {}).get("sessionUpdate")
            == "plan"
        ]
        self.assertEqual(len(plan_updates), 2)
        self.assertEqual(len(plan_updates[0]["entries"]), 1)
        self.assertEqual(len(plan_updates[1]["entries"]), 2)
        self.assertEqual(plan_updates[1]["entries"][0]["content"], "Step one")
        self.assertEqual(plan_updates[1]["entries"][1]["content"], "Step two")

    def test_runtime_action_emits_richer_tool_call_updates(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport([])
        session_runner = AcpJsonRpcSession(transport, self.app_state)

        async def _invoke() -> None:
            await session_runner._handle_runtime_event(
                session.id,
                "turn-1",
                {
                    "type": "timeline_entry",
                    "payload": {
                        "entry_kind": "action",
                        "content_json": json.dumps(
                            {
                                "toolCalls": [
                                    {
                                        "id": "call_1",
                                        "name": "read",
                                        "argumentsText": json.dumps({"path": "/tmp/x.txt"}),
                                    }
                                ],
                                "observations": ["Found file"],
                                "actionOutput": {"result": "ok"},
                                "toolDetailsByCallId": {
                                    "call_1": {
                                        "details": {
                                            "firstChangedLine": 9,
                                            "diff": "@@ -1 +1 @@",
                                        }
                                    }
                                },
                                "error": None,
                                "usage": {"inputTokens": 1, "outputTokens": 2},
                                "durationMs": 12,
                            }
                        ),
                    },
                },
            )

        asyncio.run(_invoke())
        tool_events = [
            payload for payload in transport.outgoing if payload.get("method") == "session/update"
        ]
        self.assertEqual(tool_events[0]["params"]["update"]["sessionUpdate"], "tool_call")
        self.assertEqual(tool_events[0]["params"]["update"]["kind"], "read")
        self.assertEqual(tool_events[0]["params"]["update"]["rawInput"]["arguments"]["path"], "/tmp/x.txt")
        self.assertEqual(tool_events[1]["params"]["update"]["status"], "in_progress")
        self.assertEqual(tool_events[2]["params"]["update"]["status"], "completed")
        self.assertTrue(tool_events[2]["params"]["update"]["content"])
        self.assertEqual(
            tool_events[2]["params"]["update"]["rawOutput"]["actionOutput"]["result"],
            "ok",
        )
        self.assertEqual(
            tool_events[2]["params"]["update"]["rawOutput"]["details"]["firstChangedLine"],
            9,
        )
        self.assertTrue(
            str(tool_events[2]["params"]["update"]["rawOutput"]["locations"][0]["path"]).endswith(
                "/tmp/x.txt"
            )
        )
        self.assertEqual(
            tool_events[2]["params"]["update"]["rawOutput"]["locations"][0]["line"],
            9,
        )

    def test_runtime_action_read_emits_stable_summary_and_truncation_notice(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport([])
        session_runner = AcpJsonRpcSession(transport, self.app_state)

        async def _invoke() -> None:
            await session_runner._handle_runtime_event(
                session.id,
                "turn-1",
                {
                    "type": "timeline_entry",
                    "payload": {
                        "entry_kind": "action",
                        "content_json": json.dumps(
                            {
                                "toolCalls": [
                                    {
                                        "id": "call_read",
                                        "name": "read",
                                        "argumentsText": json.dumps(
                                            {"path": "notes.txt", "offset": 7, "limit": 5}
                                        ),
                                    }
                                ],
                                "observations": ["line 7", "line 8"],
                                "actionOutput": {"result": "ok"},
                                "toolDetailsByCallId": {
                                    "call_read": {
                                        "details": {
                                            "truncation": {
                                                "truncated": True,
                                                "outputLines": 5,
                                            }
                                        }
                                    }
                                },
                                "error": None,
                            }
                        ),
                    },
                },
            )

        asyncio.run(_invoke())
        tool_events = [
            payload for payload in transport.outgoing if payload.get("method") == "session/update"
        ]
        completed_update = tool_events[2]["params"]["update"]
        raw_output = completed_update["rawOutput"]
        self.assertEqual(raw_output["locations"][0]["line"], 7)
        self.assertTrue(str(raw_output["locations"][0]["path"]).endswith("/notes.txt"))
        content_blocks = completed_update["content"]
        self.assertEqual(content_blocks[0]["content"]["text"], "Read notes.txt starting at line 7.")
        self.assertIn(
            "Output was truncated. Continue with offset=12.",
            [block["content"]["text"] for block in content_blocks],
        )

    def test_runtime_action_prefers_per_call_tool_results_content(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport([])
        session_runner = AcpJsonRpcSession(transport, self.app_state)

        async def _invoke() -> None:
            await session_runner._handle_runtime_event(
                session.id,
                "turn-1",
                {
                    "type": "timeline_entry",
                    "payload": {
                        "entry_kind": "action",
                        "content_json": json.dumps(
                            {
                                "toolCalls": [
                                    {
                                        "id": "call_1",
                                        "name": "read",
                                        "argumentsText": json.dumps({"path": "a.txt"}),
                                    },
                                    {
                                        "id": "call_2",
                                        "name": "read",
                                        "argumentsText": json.dumps({"path": "b.txt"}),
                                    },
                                ],
                                "observations": ["global observation"],
                                "actionOutput": {"result": "global"},
                                "toolResults": [
                                    {
                                        "callId": "call_1",
                                        "content": [{"type": "text", "text": "content-a"}],
                                        "details": {"firstChangedLine": 3},
                                    },
                                    {
                                        "callId": "call_2",
                                        "content": [{"type": "text", "text": "content-b"}],
                                        "details": {"firstChangedLine": 8},
                                    },
                                ],
                                "error": None,
                            }
                        ),
                    },
                },
            )

        asyncio.run(_invoke())
        updates = [
            payload["params"]["update"]
            for payload in transport.outgoing
            if payload.get("method") == "session/update"
            and payload.get("params", {}).get("update", {}).get("sessionUpdate") == "tool_call_update"
            and payload.get("params", {}).get("update", {}).get("status") == "completed"
        ]
        self.assertEqual(len(updates), 2)

        by_call_id = {str(update.get("toolCallId")): update for update in updates}
        call_1_texts = [item["content"]["text"] for item in by_call_id["call_1"]["content"]]
        call_2_texts = [item["content"]["text"] for item in by_call_id["call_2"]["content"]]
        self.assertIn("content-a", call_1_texts)
        self.assertNotIn("content-b", call_1_texts)
        self.assertIn("content-b", call_2_texts)
        self.assertNotIn("content-a", call_2_texts)
        self.assertEqual(by_call_id["call_1"]["rawOutput"]["details"]["firstChangedLine"], 3)
        self.assertEqual(by_call_id["call_2"]["rawOutput"]["details"]["firstChangedLine"], 8)

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
        agent_updates = [
            payload
            for payload in transport.outgoing
            if payload.get("method") == "session/update"
            and payload.get("params", {}).get("update", {}).get("sessionUpdate")
            == "agent_message_chunk"
        ]
        self.assertEqual(len(agent_updates), 1)
        self.assertEqual(
            agent_updates[0]["params"]["update"]["content"]["text"],
            "ok: ping",
        )

    def test_prompt_emits_final_answer_when_no_streaming_delta(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport([])
        with patch(
            "nsbot_sidecar.api.acp_session.create_runtime_engine",
            return_value=_FinalOnlyEngine(),
        ):
            async def _invoke_prompt() -> None:
                session_runner = AcpJsonRpcSession(transport, self.app_state)
                await session_runner._handle_prompt_request(
                    31,
                    {
                        "sessionId": session.id,
                        "prompt": [{"type": "text", "text": "ping"}],
                    },
                )

            asyncio.run(_invoke_prompt())
        response = _response_for(transport.outgoing, 31)
        self.assertEqual(response["result"]["stopReason"], "end_turn")
        agent_updates = [
            payload
            for payload in transport.outgoing
            if payload.get("method") == "session/update"
            and payload.get("params", {}).get("update", {}).get("sessionUpdate")
            == "agent_message_chunk"
        ]
        self.assertEqual(len(agent_updates), 1)
        self.assertEqual(
            agent_updates[0]["params"]["update"]["content"]["text"],
            "ok: ping",
        )

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

    def test_prompt_maps_runtime_error_code_to_stop_reason(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport([])
        with patch(
            "nsbot_sidecar.api.acp_session.create_runtime_engine",
            return_value=_ErrorEngine("max_tokens", "token budget exceeded"),
        ):
            async def _invoke_prompt() -> None:
                session_runner = AcpJsonRpcSession(transport, self.app_state)
                await session_runner._handle_prompt_request(
                    41,
                    {
                        "sessionId": session.id,
                        "prompt": [{"type": "text", "text": "ping"}],
                    },
                )

            asyncio.run(_invoke_prompt())
        response = _response_for(transport.outgoing, 41)
        self.assertEqual(response["result"]["stopReason"], "max_tokens")

    def test_prompt_keeps_unknown_runtime_error_as_jsonrpc_error(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport([])
        with patch(
            "nsbot_sidecar.api.acp_session.create_runtime_engine",
            return_value=_ErrorEngine("unauthorized", "Provider is missing an API key"),
        ):
            async def _invoke_prompt() -> None:
                session_runner = AcpJsonRpcSession(transport, self.app_state)
                await session_runner._handle_prompt_request(
                    42,
                    {
                        "sessionId": session.id,
                        "prompt": [{"type": "text", "text": "ping"}],
                    },
                )

            asyncio.run(_invoke_prompt())
        response = _response_for(transport.outgoing, 42)
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], -32000)

    def test_cancelled_turn_emits_cancelled_for_pending_tool_calls(self) -> None:
        session = self.app_state.repositories.sessions.create(
            workspace_id=self.workspace.id,
            active_provider_id=self.provider.provider.id,
            active_model_id="gpt-5.4",
        )
        transport = _InMemoryTransport([])
        session_runner = AcpJsonRpcSession(transport, self.app_state)

        async def _invoke() -> None:
            await session_runner._emit_session_update(
                session.id,
                {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tool_1",
                    "title": "read",
                    "kind": "read",
                    "status": "pending",
                },
                turn_id="turn-1",
            )
            await session_runner._emit_cancelled_active_tool_calls(session.id, "turn-1")

        asyncio.run(_invoke())
        updates = [
            payload["params"]["update"]
            for payload in transport.outgoing
            if payload.get("method") == "session/update"
        ]
        self.assertEqual(updates[0]["sessionUpdate"], "tool_call")
        self.assertEqual(updates[0]["status"], "pending")
        self.assertEqual(updates[1]["sessionUpdate"], "tool_call_update")
        self.assertEqual(updates[1]["toolCallId"], "tool_1")
        self.assertEqual(updates[1]["status"], "cancelled")

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
