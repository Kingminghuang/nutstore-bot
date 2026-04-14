from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nsbot_sidecar.api.api_server import ApiServerConfig, create_app
from nsbot_sidecar.runtime.runtime_service import RuntimeCancelledError


class _FakeEngine:
    def __init__(self, fn):
        self._fn = fn

    def process(
        self,
        run_id,
        user_input,
        auth_context,
        metadata,
        event_callback=None,
        is_cancelled=None,
        permission_requester=None,
    ):
        del run_id, auth_context, metadata, event_callback, is_cancelled
        return self._fn(user_input=user_input, permission_requester=permission_requester)


class _ValidationModel:
    def generate_stream(self, messages):
        del messages
        yield {"content": "OK"}


class AcpWebSocketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="sidecar-acp-"))
        self.config = ApiServerConfig(
            host="127.0.0.1",
            port=18765,
            auth_header_value="Bearer test-token",
            ns_bot_home=str(self.temp_dir),
        )
        self.client = TestClient(create_app(self.config))
        self.provider = self._create_provider()
        object.__setattr__(
            self.client.app.state.provider_service,
            "model_factory",
            lambda _cfg: _ValidationModel(),
        )
        validate_response = self.client.post(
            f"/providers/{self.provider['id']}/validate",
            headers={"Authorization": "Bearer test-token"},
            json={"modelId": "gpt-5.4"},
        )
        self.assertEqual(validate_response.status_code, 200)

    def _create_provider(self) -> dict[str, object]:
        response = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "preferredModelId": "gpt-5.4",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _create_workspace(self, name: str) -> dict[str, object]:
        workspace_dir = self.temp_dir / name
        workspace_dir.mkdir(parents=True, exist_ok=True)
        response = self.client.post(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": name,
                "realPath": str(workspace_dir),
                "pathLabel": str(workspace_dir),
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _create_session(self, workspace_id: str) -> dict[str, object]:
        response = self.client.post(
            f"/workspaces/{workspace_id}/sessions",
            headers={"Authorization": "Bearer test-token"},
            json={
                "connectionId": str(self.provider["id"]),
                "modelId": "gpt-5.4",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _send_request(self, ws, req_id: int, method: str, params: dict | None = None) -> None:
        payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        ws.send_json(payload)

    def _recv_until(self, ws, predicate):
        for _ in range(40):
            message = ws.receive_json()
            if predicate(message):
                return message
        self.fail("did not receive expected websocket message")

    def _initialize(self, ws, *, fs_read: bool = False, fs_write: bool = False) -> None:
        self._send_request(
            ws,
            1,
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": fs_read, "writeTextFile": fs_write},
                    "terminal": False,
                },
                "clientInfo": {
                    "name": "test-client",
                    "title": "ACP Test Client",
                    "version": "0.1.0",
                },
            },
        )
        init_response = self._recv_until(ws, lambda msg: msg.get("id") == 1)
        self.assertEqual(init_response["result"]["protocolVersion"], 1)

    def test_initialize_and_mode_ask_only(self) -> None:
        workspace = self.temp_dir / "ws-init"
        workspace.mkdir(parents=True, exist_ok=True)

        with self.client.websocket_connect("/acp/ws") as ws:
            self._initialize(ws)

            self._send_request(ws, 2, "session/new", {"cwd": str(workspace)})
            new_session = self._recv_until(ws, lambda msg: msg.get("id") == 2)
            self.assertIn("result", new_session)
            session_id = str(new_session["result"]["sessionId"])
            config_options = new_session["result"]["configOptions"]
            mode_option = next(item for item in config_options if item["id"] == "mode")
            self.assertEqual(mode_option["currentValue"], "ask")
            self.assertEqual([item["value"] for item in mode_option["options"]], ["ask"])

            self._send_request(
                ws,
                3,
                "session/set_config_option",
                {"sessionId": session_id, "configId": "mode", "value": "ask"},
            )
            ok_mode = self._recv_until(ws, lambda msg: msg.get("id") == 3)
            self.assertIn("result", ok_mode)

            self._send_request(
                ws,
                4,
                "session/set_config_option",
                {"sessionId": session_id, "configId": "mode", "value": "auto"},
            )
            bad_mode = self._recv_until(ws, lambda msg: msg.get("id") == 4)
            self.assertIn("error", bad_mode)

    def test_prompt_with_ask_mode_requests_permission(self) -> None:
        workspace = self._create_workspace("ws-permission")
        session = self._create_session(str(workspace["id"]))
        session_id = str(session["id"])
        decisions: list[str] = []

        def engine_impl(*, user_input: str, permission_requester):
            self.assertEqual(user_input, "please update file")
            decision = str(
                permission_requester(
                    {
                        "kind": "write",
                        "title": "Write file",
                        "toolCallId": "tool-write-1",
                    }
                )
            )
            decisions.append(decision)
            return {
                "deltas": [],
                "timeline_entries": [],
                "final_answer": "done",
            }

        with patch(
            "nsbot_sidecar.api.acp_ws.create_runtime_engine",
            return_value=_FakeEngine(engine_impl),
        ):
            with self.client.websocket_connect("/acp/ws") as ws:
                self._initialize(ws)
                self._send_request(
                    ws,
                    3,
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": "please update file"}],
                        "_meta": {"autoAllow": False},
                    },
                )

                permission_request = self._recv_until(
                    ws, lambda msg: msg.get("method") == "session/request_permission"
                )
                rpc_id = permission_request["id"]
                ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {
                            "outcome": {
                                "outcome": "selected",
                                "optionId": "allow-once",
                            }
                        },
                    }
                )

                prompt_result = self._recv_until(ws, lambda msg: msg.get("id") == 3)
                self.assertEqual(prompt_result["result"]["stopReason"], "end_turn")
                self.assertEqual(decisions, ["allow"])

    def test_cancel_pending_permission_converges_to_cancelled(self) -> None:
        workspace = self._create_workspace("ws-cancel")
        session = self._create_session(str(workspace["id"]))
        session_id = str(session["id"])

        def engine_impl(*, user_input: str, permission_requester):
            self.assertEqual(user_input, "cancel me")
            decision = str(
                permission_requester(
                    {
                        "kind": "edit",
                        "title": "Edit file",
                        "toolCallId": "tool-edit-1",
                    }
                )
            )
            if decision == "cancelled":
                raise RuntimeCancelledError()
            return {
                "deltas": [],
                "timeline_entries": [],
                "final_answer": "should not complete",
            }

        with patch(
            "nsbot_sidecar.api.acp_ws.create_runtime_engine",
            return_value=_FakeEngine(engine_impl),
        ):
            with self.client.websocket_connect("/acp/ws") as ws:
                self._initialize(ws)
                self._send_request(
                    ws,
                    3,
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": "cancel me"}],
                        "_meta": {"autoAllow": False},
                    },
                )
                self._recv_until(ws, lambda msg: msg.get("method") == "session/request_permission")
                ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/cancel",
                        "params": {"sessionId": session_id},
                    }
                )
                prompt_result = self._recv_until(ws, lambda msg: msg.get("id") == 3)
                self.assertEqual(prompt_result["result"]["stopReason"], "cancelled")

    def test_prompt_resource_link_uses_client_fs_read_text_file(self) -> None:
        workspace = self._create_workspace("ws-resource")
        session = self._create_session(str(workspace["id"]))
        session_id = str(session["id"])
        captured_input: list[str] = []

        def engine_impl(*, user_input: str, permission_requester):
            del permission_requester
            captured_input.append(user_input)
            return {
                "deltas": [],
                "timeline_entries": [],
                "final_answer": "ok",
            }

        with patch(
            "nsbot_sidecar.api.acp_ws.create_runtime_engine",
            return_value=_FakeEngine(engine_impl),
        ):
            with self.client.websocket_connect("/acp/ws") as ws:
                self._initialize(ws, fs_read=True, fs_write=True)
                self._send_request(
                    ws,
                    3,
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [
                            {
                                "type": "resource_link",
                                "uri": "file:///tmp/acp-test.txt",
                            }
                        ],
                    },
                )

                fs_request = self._recv_until(
                    ws, lambda msg: msg.get("method") == "fs/read_text_file"
                )
                self.assertIn("/tmp/acp-test.txt", str(fs_request.get("params", {}).get("path")))
                ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": fs_request["id"],
                        "result": {"content": "content-from-file"},
                    }
                )
                prompt_result = self._recv_until(ws, lambda msg: msg.get("id") == 3)
                self.assertEqual(prompt_result["result"]["stopReason"], "end_turn")
                self.assertEqual(captured_input, ["content-from-file"])

    def test_auto_allow_true_does_not_emit_permission_request(self) -> None:
        workspace = self._create_workspace("ws-auto-allow")
        session = self._create_session(str(workspace["id"]))
        session_id = str(session["id"])
        decisions: list[str] = []

        def engine_impl(*, user_input: str, permission_requester):
            self.assertEqual(user_input, "auto allow")
            decisions.append(
                str(
                    permission_requester(
                        {
                            "kind": "python_exec_agent",
                            "title": "Execute python code",
                            "toolCallId": "tool-python-1",
                        }
                    )
                )
            )
            return {"deltas": [], "timeline_entries": [], "final_answer": "ok"}

        with patch(
            "nsbot_sidecar.api.acp_ws.create_runtime_engine",
            return_value=_FakeEngine(engine_impl),
        ):
            with self.client.websocket_connect("/acp/ws") as ws:
                self._initialize(ws)
                self._send_request(
                    ws,
                    3,
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": "auto allow"}],
                        "_meta": {"autoAllow": True},
                    },
                )

                saw_permission_request = False
                for _ in range(20):
                    message = ws.receive_json()
                    if message.get("method") == "session/request_permission":
                        saw_permission_request = True
                    if message.get("id") == 3:
                        self.assertEqual(message["result"]["stopReason"], "end_turn")
                        break
                self.assertFalse(saw_permission_request)
                self.assertEqual(decisions, ["allow"])

    def test_session_list_load_and_resume(self) -> None:
        workspace = self._create_workspace("ws-list")
        session = self._create_session(str(workspace["id"]))
        session_id = str(session["id"])
        self.client.app.state.repositories.timeline_entries.append(
            session_id=session_id,
            run_id=None,
            entry_kind="user_input",
            display_role="user",
            content_text="hello from history",
        )

        with self.client.websocket_connect("/acp/ws") as ws:
            self._initialize(ws)

            self._send_request(ws, 2, "session/list", {"cwd": workspace["realPath"]})
            list_resp = self._recv_until(ws, lambda msg: msg.get("id") == 2)
            self.assertIn("result", list_resp)
            sessions = list_resp["result"]["sessions"]
            self.assertTrue(any(item["sessionId"] == session_id for item in sessions))

            self._send_request(ws, 3, "session/load", {"sessionId": session_id})
            history_notice = self._recv_until(
                ws,
                lambda msg: msg.get("method") == "session/update"
                and msg.get("params", {}).get("update", {}).get("sessionUpdate")
                == "user_message_chunk",
            )
            self.assertEqual(
                history_notice["params"]["update"]["content"]["text"],
                "hello from history",
            )
            load_resp = self._recv_until(ws, lambda msg: msg.get("id") == 3)
            self.assertIn("result", load_resp)

            self._send_request(ws, 4, "session/resume", {"sessionId": session_id})
            resume_resp = self._recv_until(ws, lambda msg: msg.get("id") == 4)
            self.assertIn("result", resume_resp)

    def test_edit_and_prompt_rewrites_suffix_and_runs(self) -> None:
        workspace = self._create_workspace("ws-edit-and-prompt")
        session = self._create_session(str(workspace["id"]))
        session_id = str(session["id"])

        self.client.app.state.repositories.timeline_entries.append(
            session_id=session_id,
            timeline_entry_id="msg_keep_1",
            run_id=None,
            entry_kind="user_input",
            display_role="user",
            content_text="prefix user",
            sequence_no=1,
            created_at="2026-03-24T12:00:00Z",
        )
        self.client.app.state.repositories.timeline_entries.append(
            session_id=session_id,
            timeline_entry_id="msg_keep_2",
            run_id=None,
            entry_kind="final_answer",
            display_role="assistant",
            content_text="prefix assistant",
            sequence_no=2,
            created_at="2026-03-24T12:00:10Z",
        )
        self.client.app.state.repositories.timeline_entries.append(
            session_id=session_id,
            timeline_entry_id="msg_edit_1",
            run_id=None,
            entry_kind="user_input",
            display_role="user",
            content_text="old editable",
            sequence_no=3,
            created_at="2026-03-24T12:01:00Z",
        )
        self.client.app.state.repositories.timeline_entries.append(
            session_id=session_id,
            timeline_entry_id="msg_drop_1",
            run_id=None,
            entry_kind="final_answer",
            display_role="assistant",
            content_text="old assistant tail",
            sequence_no=4,
            created_at="2026-03-24T12:01:10Z",
        )
        self.client.app.state.session_service.timeline_service.refresh_session_summary(
            session_id
        )

        def engine_impl(*, user_input: str, permission_requester):
            del permission_requester
            self.assertEqual(user_input, "edited user message")
            return {"deltas": [], "timeline_entries": [], "final_answer": "Edited run complete"}

        with patch(
            "nsbot_sidecar.api.acp_ws.create_runtime_engine",
            return_value=_FakeEngine(engine_impl),
        ):
            with self.client.websocket_connect("/acp/ws") as ws:
                self._initialize(ws)
                self._send_request(
                    ws,
                    3,
                    "session/edit_and_prompt",
                    {
                        "sessionId": session_id,
                        "entryId": "msg_edit_1",
                        "prompt": [{"type": "text", "text": "edited user message"}],
                        "_meta": {"autoAllow": True},
                    },
                )
                prompt_result = self._recv_until(ws, lambda msg: msg.get("id") == 3)
                self.assertIn("result", prompt_result)
                self.assertEqual(prompt_result["result"]["stopReason"], "end_turn")

        timeline = self.client.get(
            f"/sessions/{session_id}/timeline",
            headers={"Authorization": "Bearer test-token"},
        ).json()["entries"]
        self.assertEqual(
            [item["contentText"] for item in timeline],
            ["prefix user", "prefix assistant", "edited user message", "Edited run complete"],
        )

    def test_edit_and_prompt_rejects_non_user_message(self) -> None:
        workspace = self._create_workspace("ws-edit-invalid-role")
        session = self._create_session(str(workspace["id"]))
        session_id = str(session["id"])

        self.client.app.state.repositories.timeline_entries.append(
            session_id=session_id,
            timeline_entry_id="msg_assistant_1",
            run_id=None,
            entry_kind="final_answer",
            display_role="assistant",
            content_text="assistant content",
            sequence_no=1,
            created_at="2026-03-24T12:00:00Z",
        )

        with self.client.websocket_connect("/acp/ws") as ws:
            self._initialize(ws)
            self._send_request(
                ws,
                3,
                "session/edit_and_prompt",
                {
                    "sessionId": session_id,
                    "entryId": "msg_assistant_1",
                    "prompt": [{"type": "text", "text": "new content"}],
                    "_meta": {"autoAllow": True},
                },
            )
            response = self._recv_until(ws, lambda msg: msg.get("id") == 3)
            self.assertIn("error", response)
            self.assertIn(
                "Only user input timeline entries can be edited",
                response["error"]["message"],
            )

    def test_permission_reject_returns_prompt_error(self) -> None:
        workspace = self._create_workspace("ws-reject")
        session = self._create_session(str(workspace["id"]))
        session_id = str(session["id"])

        def engine_impl(*, user_input: str, permission_requester):
            self.assertEqual(user_input, "reject this")
            decision = str(
                permission_requester(
                    {
                        "kind": "write",
                        "title": "Write file",
                        "toolCallId": "tool-write-reject",
                    }
                )
            )
            if decision != "allow":
                raise RuntimeError("permission_denied")
            return {"deltas": [], "timeline_entries": [], "final_answer": "unexpected"}

        with patch(
            "nsbot_sidecar.api.acp_ws.create_runtime_engine",
            return_value=_FakeEngine(engine_impl),
        ):
            with self.client.websocket_connect("/acp/ws") as ws:
                self._initialize(ws)
                self._send_request(
                    ws,
                    3,
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": "reject this"}],
                        "_meta": {"autoAllow": False},
                    },
                )
                permission_request = self._recv_until(
                    ws, lambda msg: msg.get("method") == "session/request_permission"
                )
                ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": permission_request["id"],
                        "result": {
                            "outcome": {
                                "outcome": "selected",
                                "optionId": "reject-once",
                            }
                        },
                    }
                )
                prompt_result = self._recv_until(ws, lambda msg: msg.get("id") == 3)
                self.assertIn("error", prompt_result)

    def test_set_model_and_thought_level_updates_config(self) -> None:
        workspace = self._create_workspace("ws-config")
        session = self._create_session(str(workspace["id"]))
        session_id = str(session["id"])

        with self.client.websocket_connect("/acp/ws") as ws:
            self._initialize(ws)
            self._send_request(
                ws,
                2,
                "session/set_config_option",
                {
                    "sessionId": session_id,
                    "configId": "thought_level",
                    "value": "high",
                },
            )
            thought_resp = self._recv_until(ws, lambda msg: msg.get("id") == 2)
            self.assertIn("result", thought_resp)
            thought_option = next(
                item
                for item in thought_resp["result"]["configOptions"]
                if item["id"] == "thought_level"
            )
            self.assertEqual(thought_option["currentValue"], "high")

            self._send_request(
                ws,
                3,
                "session/set_config_option",
                {
                    "sessionId": session_id,
                    "configId": "model",
                    "value": "gpt-5.4-mini",
                },
            )
            model_resp = self._recv_until(ws, lambda msg: msg.get("id") == 3)
            self.assertIn("result", model_resp)
            model_option = next(
                item for item in model_resp["result"]["configOptions"] if item["id"] == "model"
            )
            self.assertEqual(model_option["currentValue"], "gpt-5.4-mini")

    def test_session_list_filters_by_cwd(self) -> None:
        workspace_one = self._create_workspace("ws-list-a")
        workspace_two = self._create_workspace("ws-list-b")
        session_one = self._create_session(str(workspace_one["id"]))
        self._create_session(str(workspace_two["id"]))

        with self.client.websocket_connect("/acp/ws") as ws:
            self._initialize(ws)
            self._send_request(
                ws,
                2,
                "session/list",
                {"cwd": workspace_one["realPath"]},
            )
            list_resp = self._recv_until(ws, lambda msg: msg.get("id") == 2)
            self.assertIn("result", list_resp)
            sessions = list_resp["result"]["sessions"]
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["sessionId"], session_one["id"])

    def test_method_aliases_for_session_management(self) -> None:
        workspace = self._create_workspace("ws-alias")
        session = self._create_session(str(workspace["id"]))
        session_id = str(session["id"])

        with self.client.websocket_connect("/acp/ws") as ws:
            self._initialize(ws)

            self._send_request(ws, 2, "listSessions", {"cwd": workspace["realPath"]})
            list_resp = self._recv_until(ws, lambda msg: msg.get("id") == 2)
            self.assertIn("result", list_resp)
            self.assertTrue(
                any(item["sessionId"] == session_id for item in list_resp["result"]["sessions"])
            )

            self._send_request(ws, 3, "resumeSession", {"sessionId": session_id})
            resume_resp = self._recv_until(ws, lambda msg: msg.get("id") == 3)
            self.assertIn("result", resume_resp)

            self._send_request(ws, 4, "loadSession", {"sessionId": session_id})
            load_resp = self._recv_until(ws, lambda msg: msg.get("id") == 4)
            self.assertIn("result", load_resp)

    def test_authenticate_and_disconnect(self) -> None:
        with self.client.websocket_connect("/acp/ws") as ws:
            self._initialize(ws)
            self._send_request(
                ws,
                2,
                "authenticate",
                {"method": "api-key", "payload": {"apiKey": "sk-test"}},
            )
            auth_resp = self._recv_until(ws, lambda msg: msg.get("id") == 2)
            self.assertIn("result", auth_resp)

            self._send_request(ws, 3, "disconnect", {})
            try:
                disconnect_resp = ws.receive_json()
                if isinstance(disconnect_resp, dict) and disconnect_resp.get("id") == 3:
                    self.assertIn("result", disconnect_resp)
            except (WebSocketDisconnect, RuntimeError):
                # Server may close immediately after handling disconnect.
                pass

    def test_prompt_resource_block_embedded_text(self) -> None:
        workspace = self._create_workspace("ws-resource-embedded")
        session = self._create_session(str(workspace["id"]))
        session_id = str(session["id"])
        captured_input: list[str] = []

        def engine_impl(*, user_input: str, permission_requester):
            del permission_requester
            captured_input.append(user_input)
            return {"deltas": [], "timeline_entries": [], "final_answer": "ok"}

        with patch(
            "nsbot_sidecar.api.acp_ws.create_runtime_engine",
            return_value=_FakeEngine(engine_impl),
        ):
            with self.client.websocket_connect("/acp/ws") as ws:
                self._initialize(ws)
                self._send_request(
                    ws,
                    3,
                    "prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [
                            {
                                "type": "resource",
                                "resource": {
                                    "uri": "file:///tmp/embedded.txt",
                                    "mimeType": "text/plain",
                                    "text": "embedded-content",
                                },
                            }
                        ],
                    },
                )
                prompt_resp = self._recv_until(ws, lambda msg: msg.get("id") == 3)
                self.assertIn("result", prompt_resp)
                self.assertEqual(prompt_resp["result"]["stopReason"], "end_turn")
                self.assertEqual(captured_input, ["embedded-content"])


if __name__ == "__main__":
    unittest.main()
