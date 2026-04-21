from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from nsbot_sidecar import cli as cli_module
from nsbot_sidecar.cli import main as cli_main
from nsbot_sidecar.providers.provider_catalog import list_providers
from nsbot_sidecar.infrastructure.repositories import create_repositories
from nsbot_sidecar.infrastructure.storage import connect_database


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli_main(argv)
    return code, out.getvalue(), err.getvalue()


class CliProviderModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sidecar-cli-")
        self.workspace_dir = Path(self.temp_dir) / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.connection = connect_database(self.temp_dir)
        self.repositories = create_repositories(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_builtin_openai(self) -> str:
        bundle = self.repositories.providers.save_bundle(
            provider_data={
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "base_url": None,
                "secret_ref": "sec_test_openai",
                "preferred_model_id": None,
            },
            models=[],
        )
        return bundle.provider.id

    def _create_workspace(self) -> str:
        workspace = self.repositories.workspaces.create(
            name="Workspace",
            path_label=str(self.workspace_dir),
            real_path=str(self.workspace_dir),
        )
        return workspace.id

    def test_providers_use_command_is_removed(self) -> None:
        code, stdout, stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "providers",
                "use",
                "openai",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("No such command 'use'", stderr)

    def test_providers_delete_requires_provider_id(self) -> None:
        connection_id = self._create_builtin_openai()
        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "providers",
                "delete",
                "--provider-id",
                connection_id,
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["deletedProviderId"], connection_id)
        self.assertIsNone(self.repositories.providers.get_bundle_by_id(connection_id))

    def test_models_create_persists_custom_provider(self) -> None:
        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "models",
                "create",
                "--name",
                "my-gateway",
                "--base-url",
                "https://llm.example.com/v1",
                "--model-id",
                "model-a",
                "--api-key",
                "sk-test",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["kind"], "custom")
        self.assertEqual(payload["customSlug"], "my-gateway")
        self.assertEqual(payload["preferredModelId"], "model-a")

    def test_models_get_returns_provider_model_tuple(self) -> None:
        bundle = self.repositories.providers.save_bundle(
            provider_data={
                "runtime_provider": "custom",
                "catalog_provider_id": None,
                "id": "my-gateway",
                "display_name": "My Gateway",
                "base_url": "https://llm.example.com/v1",
                "secret_ref": "sec_test_custom",
                "preferred_model_id": "model-a",
            },
            models=[
                {
                    "model_id": "model-a",
                    "display_name": "Model A",
                },
            ],
        )

        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "models",
                "get",
                f"{bundle.provider.id}:model-a",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["providerId"], bundle.provider.id)
        self.assertEqual(payload["modelId"], "model-a")

    def test_models_set_default_updates_global_selection(self) -> None:
        connection_id = self._create_builtin_openai()
        openai_entry = next(
            item for item in list_providers() if str(item.get("id")) == "openai"
        )
        model_ids = [str(item.get("id")) for item in openai_entry.get("models", [])]
        self.assertGreater(len(model_ids), 0)

        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "models",
                "set-default",
                f"{connection_id}:{model_ids[0]}",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["action"], "set-default")
        self.assertEqual(payload["providerId"], connection_id)
        self.assertEqual(payload["modelId"], model_ids[0])

        selection = self.repositories.default_model_selection.get()
        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.provider_id, connection_id)
        self.assertEqual(selection.model_id, model_ids[0])

    def test_models_list_works_without_provider_filter(self) -> None:
        self._create_builtin_openai()

        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "models",
                "list",
            ]
        )

        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertIn("groups", payload)
        self.assertIsInstance(payload["groups"], list)

    def test_cli_bootstraps_templates_into_ns_bot_home(self) -> None:
        source_dir = Path(tempfile.mkdtemp(prefix="sidecar-templates-"))
        try:
            for filename in ("IDENTITFY.md", "SOUL.md", "USER.md", "TOOLS.md"):
                (source_dir / filename).write_text(f"{filename} content", encoding="utf-8")

            templates_dir = Path(self.temp_dir) / "templates"
            shutil.rmtree(templates_dir, ignore_errors=True)

            with mock.patch.dict(
                os.environ,
                {"NSBOT_TEMPLATES_SOURCE": str(source_dir)},
                clear=False,
            ):
                code, _stdout, _stderr = _run_cli(
                    [
                        "--ns-bot-home",
                        self.temp_dir,
                        "providers",
                        "list",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertTrue((templates_dir / "IDENTITFY.md").exists())
            self.assertTrue((templates_dir / "SOUL.md").exists())
            self.assertTrue((templates_dir / "USER.md").exists())
            self.assertTrue((templates_dir / "TOOLS.md").exists())
        finally:
            shutil.rmtree(source_dir, ignore_errors=True)

    def test_models_status_command_is_removed(self) -> None:
        code, stdout, stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "models",
                "status",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("No such command 'status'", stderr)

    def test_models_remove_can_infer_provider_id_when_unique(self) -> None:
        bundle = self.repositories.providers.save_bundle(
            provider_data={
                "runtime_provider": "custom",
                "catalog_provider_id": None,
                "id": "demo-gateway",
                "display_name": "Demo Gateway",
                "base_url": "https://llm.example.com/v1",
                "secret_ref": "sec_test_custom",
                "preferred_model_id": "demo-model-alpha",
            },
            models=[
                {
                    "model_id": "demo-model-alpha",
                    "display_name": "Demo Model Alpha",
                },
                {
                    "model_id": "demo-model-beta",
                    "display_name": "Demo Model Beta",
                },
            ],
        )

        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "models",
                "remove",
                "--model",
                "demo-model-alpha",
            ]
        )

        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["providerId"], bundle.provider.id)
        self.assertEqual(payload["modelId"], "demo-model-alpha")

    def test_sessions_group_is_removed(self) -> None:
        code, stdout, stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "sessions",
                "--help",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("No such command 'sessions'", stderr)

    def test_threads_get_help_uses_thread_id_flag(self) -> None:
        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "threads",
                "get",
                "--help",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("--thread-id", stdout)

    def test_agent_threads_and_thread_get_commands_are_removed(self) -> None:
        code_threads, stdout_threads, stderr_threads = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "agent",
                "threads",
            ]
        )
        self.assertEqual(code_threads, 2)
        self.assertEqual(stdout_threads, "")
        self.assertIn("No such command 'threads'", stderr_threads)

        code_get, stdout_get, stderr_get = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "agent",
                "thread-get",
            ]
        )
        self.assertEqual(code_get, 2)
        self.assertEqual(stdout_get, "")
        self.assertIn("No such command 'thread-get'", stderr_get)

    def test_agent_run_background_returns_workspace_and_thread_ids(self) -> None:
        with mock.patch.object(
            cli_module,
            "_resolve_run_target",
            return_value=(
                object(),
                {"mode": "default-provider"},
                "provider_x",
                "model_x",
            ),
        ), mock.patch.object(
            cli_module,
            "_resolve_thread_context",
            return_value=(
                "thread_x",
                cli_module.RunMetadata(workspace_path="/tmp/workspace", session_key=None),
                {"workspaceId": "workspace_x"},
            ),
        ), mock.patch.object(
            cli_module.subprocess,
            "Popen",
            return_value=mock.Mock(pid=12345),
        ):
            code, stdout, stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "agent",
                    "run",
                    "--prompt",
                    "hello",
                    "--background",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["workspace_id"], "workspace_x")
        self.assertEqual(payload["thread_id"], "thread_x")

    def test_workspaces_index_submit_creates_background_task(self) -> None:
        workspace_id = self._create_workspace()

        with mock.patch.object(
            cli_module.uuid,
            "uuid4",
            return_value=mock.Mock(hex="abc123"),
        ), mock.patch.object(
            cli_module.subprocess,
            "Popen",
            return_value=mock.Mock(pid=4321),
        ):
            code, stdout, stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "workspaces",
                    "index",
                    "submit",
                    "--workspace-id",
                    workspace_id,
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["taskId"], "index_abc123")
        self.assertEqual(payload["workspaceId"], workspace_id)
        self.assertEqual(payload["status"], "pending")
        self.assertFalse(payload["reused"])
        self.assertEqual(payload["pid"], 4321)
        record = cli_module._read_index_task_record(self.temp_dir, "index_abc123")
        self.assertEqual(record["workspaceId"], workspace_id)

    def test_workspaces_index_submit_reuses_active_task(self) -> None:
        workspace_id = self._create_workspace()
        cli_module._write_index_task_record(
            self.temp_dir,
            "index_existing",
            {
                "taskId": "index_existing",
                "workspaceId": workspace_id,
                "workspacePath": str(self.workspace_dir),
                "status": "running",
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-01-01T00:00:00+00:00",
                "startedAt": "2026-01-01T00:00:00+00:00",
                "finishedAt": None,
                "pid": 9001,
                "error": None,
            },
        )

        with mock.patch.object(cli_module.subprocess, "Popen") as popen:
            code, stdout, stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "workspaces",
                    "index",
                    "submit",
                    "--workspace-id",
                    workspace_id,
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["taskId"], "index_existing")
        self.assertTrue(payload["reused"])
        popen.assert_not_called()

    def test_workspaces_index_status_reads_task_record(self) -> None:
        cli_module._write_index_task_record(
            self.temp_dir,
            "index_status",
            {
                "taskId": "index_status",
                "workspaceId": "workspace_x",
                "workspacePath": str(self.workspace_dir),
                "status": "succeeded",
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-01-01T00:01:00+00:00",
                "startedAt": "2026-01-01T00:00:10+00:00",
                "finishedAt": "2026-01-01T00:01:00+00:00",
                "pid": 123,
                "error": None,
            },
        )

        with mock.patch.object(
            cli_module,
            "_index_manifest_payload",
            return_value={"status": "indexed", "stats": {"converted": 1}},
        ):
            code, stdout, stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "workspaces",
                    "index",
                    "status",
                    "--task-id",
                    "index_status",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["taskId"], "index_status")
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["manifest"]["status"], "indexed")

    def test_workspaces_index_status_reads_workspace_manifest_payload(self) -> None:
        workspace_id = self._create_workspace()

        with mock.patch.object(
            cli_module,
            "_build_session_service",
        ) as build_session_service:
            database = mock.Mock()
            session_service = mock.Mock()
            session_service.workspace_index_status_payload.return_value = {
                "workspaceId": workspace_id,
                "status": "indexed",
                "stats": {"converted": 3},
            }
            build_session_service.return_value = (database, mock.Mock(), session_service)

            code, stdout, stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "workspaces",
                    "index",
                    "status",
                    "--workspace-id",
                    workspace_id,
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["workspaceId"], workspace_id)
        self.assertEqual(payload["status"], "indexed")
        self.assertEqual(payload["stats"]["converted"], 3)
        database.close.assert_called_once()

    def test_workspaces_index_cancel_prints_canceled_and_updates_task(self) -> None:
        cli_module._write_index_task_record(
            self.temp_dir,
            "index_cancel",
            {
                "taskId": "index_cancel",
                "workspaceId": "workspace_x",
                "workspacePath": str(self.workspace_dir),
                "status": "running",
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-01-01T00:01:00+00:00",
                "startedAt": "2026-01-01T00:00:10+00:00",
                "finishedAt": None,
                "pid": 123,
                "error": None,
            },
        )
        pid_file = cli_module._index_task_pid_file(self.temp_dir, "index_cancel")
        pid_file.write_text("4242", encoding="utf-8")

        with mock.patch.object(cli_module.os, "kill") as kill:
            code, stdout, stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "workspaces",
                    "index",
                    "cancel",
                    "--task-id",
                    "index_cancel",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "Canceled")
        self.assertEqual(stderr, "")
        kill.assert_called_once_with(4242, 15)
        self.assertFalse(pid_file.exists())
        payload = cli_module._read_index_task_record(self.temp_dir, "index_cancel")
        self.assertEqual(payload["status"], "canceled")
        self.assertIsNotNone(payload["finishedAt"])
        self.assertIsNone(payload["pid"])

    def test_workspaces_index_cancel_help_uses_task_id_flag(self) -> None:
        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "workspaces",
                "index",
                "cancel",
                "--help",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("--task-id", stdout)

    def test_workspaces_sidecar_index_status_command_is_removed(self) -> None:
        code, _stdout, stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "workspaces",
                "sidecar-index-status",
                "--workspace-id",
                "workspace_x",
            ]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("No such command", stderr)

    def test_workspaces_index_worker_updates_task_record(self) -> None:
        cli_module._write_index_task_record(
            self.temp_dir,
            "index_worker",
            {
                "taskId": "index_worker",
                "workspaceId": "workspace_x",
                "workspacePath": str(self.workspace_dir),
                "status": "pending",
                "createdAt": "2026-01-01T00:00:00+00:00",
                "updatedAt": "2026-01-01T00:00:00+00:00",
                "startedAt": None,
                "finishedAt": None,
                "pid": None,
                "error": None,
            },
        )

        indexer = mock.Mock()
        indexer.status.return_value = {"status": "indexed", "stats": {"converted": 2}}
        with mock.patch.object(cli_module, "WorkspaceIndexer", return_value=indexer):
            code, stdout, stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "workspaces",
                    "index",
                    "worker",
                    "--task-id",
                    "index_worker",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        record = cli_module._read_index_task_record(self.temp_dir, "index_worker")
        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["manifest"]["stats"]["converted"], 2)

    def test_codex_adapter_maps_plan_to_todo_and_action_thought_to_reasoning(self) -> None:
        runtime_events = [
            {
                "type": "runtime_step",
                "payload": {
                    "step_kind": "planning",
                    "step_id": "step-1",
                    "content_text": "1. Search repo\n2. Implement adapter",
                },
            },
            {
                "type": "runtime_step",
                "payload": {
                    "step_kind": "action",
                    "step_id": "step-2",
                    "content_json": json.dumps(
                        {
                            "thought": "I should inspect the CLI flow first.",
                            "toolCalls": [],
                            "usage": {"inputTokens": 2, "outputTokens": 3},
                        }
                    ),
                },
            },
        ]

        events = cli_module._build_codex_thread_events(
            thread_id="thread_x",
            turn_id="run_x",
            runtime_events=runtime_events,
            runtime_result={"final_answer": None},
        )

        self.assertEqual(events[0]["type"], "thread.started")
        self.assertEqual(events[1]["type"], "turn.started")
        todo_started = next(
            event
            for event in events
            if event["type"] == "item.started"
            and event["item"]["type"] == "todo_list"
        )
        self.assertEqual(
            [item["text"] for item in todo_started["item"]["items"]],
            ["Search repo", "Implement adapter"],
        )
        reasoning_completed = next(
            event
            for event in events
            if event["type"] == "item.completed"
            and event["item"]["type"] == "reasoning"
        )
        self.assertEqual(
            reasoning_completed["item"]["text"],
            "I should inspect the CLI flow first.",
        )
        self.assertEqual(events[-1]["type"], "turn.completed")
        self.assertEqual(events[-1]["usage"]["input_tokens"], 2)
        self.assertEqual(events[-1]["usage"]["output_tokens"], 3)

    def test_codex_adapter_maps_tool_families_and_final_answer_fallback(self) -> None:
        runtime_events = [
            {
                "type": "runtime_step",
                "payload": {
                    "step_kind": "action",
                    "step_id": "step-tools",
                    "content_json": json.dumps(
                        {
                            "toolCalls": [
                                {
                                    "id": "call_grep",
                                    "name": "grep",
                                    "argumentsText": json.dumps(
                                        {"pattern": "todo", "path": "docs"}
                                    ),
                                },
                                {
                                    "id": "call_find",
                                    "name": "find",
                                    "argumentsText": json.dumps(
                                        {"pattern": "*.md", "path": "."}
                                    ),
                                },
                                {
                                    "id": "call_read",
                                    "name": "read",
                                    "argumentsText": json.dumps({"path": "README.md"}),
                                },
                                {
                                    "id": "call_write",
                                    "name": "write",
                                    "argumentsText": json.dumps(
                                        {"path": "notes.txt", "content": "hello"}
                                    ),
                                },
                                {
                                    "id": "call_py",
                                    "name": "python_exec_agent",
                                    "argumentsText": json.dumps({"code": "print(1)"}),
                                },
                            ],
                            "toolResults": [
                                {
                                    "callId": "call_grep",
                                    "content": [
                                        {"type": "text", "text": "docs/a.txt:1: todo"}
                                    ],
                                },
                                {
                                    "callId": "call_find",
                                    "content": [{"type": "text", "text": "README.md"}],
                                },
                                {
                                    "callId": "call_read",
                                    "content": [{"type": "text", "text": "file content"}],
                                },
                                {
                                    "callId": "call_write",
                                    "content": [{"type": "text", "text": "write ok"}],
                                    "details": {"mutationKind": "add"},
                                },
                                {
                                    "callId": "call_py",
                                    "content": [{"type": "text", "text": "1"}],
                                },
                            ],
                            "usage": {"inputTokens": 4, "outputTokens": 6},
                        }
                    ),
                },
            }
        ]

        events = cli_module._build_codex_thread_events(
            thread_id="thread_x",
            turn_id="run_tools",
            runtime_events=runtime_events,
            runtime_result={"final_answer": "done"},
        )

        grep_started = next(
            event
            for event in events
            if event["type"] == "item.started" and event["item"]["id"] == "call_grep"
        )
        self.assertEqual(grep_started["item"]["type"], "command_execution")
        self.assertEqual(grep_started["item"]["command"], "rg todo docs")

        find_completed = next(
            event
            for event in events
            if event["type"] == "item.completed" and event["item"]["id"] == "call_find"
        )
        self.assertEqual(find_completed["item"]["type"], "command_execution")
        self.assertEqual(find_completed["item"]["exit_code"], 0)

        read_completed = next(
            event
            for event in events
            if event["type"] == "item.completed" and event["item"]["id"] == "call_read"
        )
        self.assertEqual(read_completed["item"]["type"], "mcp_tool_call")
        self.assertEqual(read_completed["item"]["tool"], "read")

        write_completed = next(
            event
            for event in events
            if event["type"] == "item.completed" and event["item"]["id"] == "call_write"
        )
        self.assertEqual(write_completed["item"]["type"], "file_change")
        self.assertEqual(write_completed["item"]["changes"][0]["path"], "notes.txt")
        self.assertEqual(write_completed["item"]["changes"][0]["kind"], "add")

        py_completed = next(
            event
            for event in events
            if event["type"] == "item.completed" and event["item"]["id"] == "call_py"
        )
        self.assertEqual(py_completed["item"]["type"], "command_execution")
        self.assertEqual(py_completed["item"]["command"], "python_exec_agent print(1)")

        final_message = next(
            event
            for event in events
            if event["type"] == "item.completed"
            and event["item"]["type"] == "agent_message"
        )
        self.assertEqual(final_message["item"]["text"], "done")

    def test_agent_run_json_returns_codex_thread_events(self) -> None:
        class _FakeDb:
            def close(self) -> None:
                return None

        class _FakeSessionService:
            def list_timeline_payload(self, _session_id: str) -> dict[str, object]:
                return {"events": []}

        runtime_events = [
            {
                "type": "delta",
                "payload": {"step_id": "step-1", "text": "Hello"},
            }
        ]
        with mock.patch.object(
            cli_module,
            "_resolve_run_target",
            return_value=(
                object(),
                {"mode": "default-provider"},
                "provider_x",
                "model_x",
            ),
        ), mock.patch.object(
            cli_module,
            "_resolve_thread_context",
            return_value=(
                "thread_x",
                cli_module.RunMetadata(workspace_path="/tmp/workspace", session_key=None),
                {"workspaceId": "workspace_x"},
            ),
        ), mock.patch.object(
            cli_module,
            "_execute_agent_turn",
            return_value={
                "runtimeEvents": runtime_events,
                "result": {"final_answer": "Hello", "deltas": [], "session_messages": []},
                "finalAnswer": "Hello",
            },
        ), mock.patch.object(
            cli_module,
            "_build_session_service",
            return_value=(_FakeDb(), object(), _FakeSessionService()),
        ):
            code, stdout, stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "agent",
                    "run",
                    "--prompt",
                    "hello",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["thread_id"], "thread_x")
        self.assertEqual(payload["events"][0]["type"], "thread.started")
        self.assertEqual(payload["events"][1]["type"], "turn.started")
        completed_message = next(
            event
            for event in payload["events"]
            if event["type"] == "item.completed"
            and event["item"]["type"] == "agent_message"
        )
        self.assertEqual(completed_message["item"]["text"], "Hello")
        self.assertEqual(payload["final_answer"], "Hello")
        self.assertNotIn("timeline", payload)
        self.assertNotIn("deprecated", payload)

    def test_agent_run_json_can_include_deprecated_timeline_rows(self) -> None:
        run_id = "run_1234567890abcdef"

        class _FakeDb:
            def close(self) -> None:
                return None

        class _FakeSessionService:
            def list_timeline_payload(self, _session_id: str) -> dict[str, object]:
                return {
                    "events": [
                        {
                            "sequenceNo": 1,
                            "turnId": run_id,
                            "eventType": "acp.event",
                            "payload": {"type": "turn.started"},
                            "createdAt": "2026-01-01T00:00:00Z",
                        }
                    ]
                }

        runtime_events = [
            {
                "type": "delta",
                "payload": {"step_id": "step-1", "text": "Hello"},
            }
        ]
        with mock.patch.object(
            cli_module,
            "_resolve_run_target",
            return_value=(
                object(),
                {"mode": "default-provider"},
                "provider_x",
                "model_x",
            ),
        ), mock.patch.object(
            cli_module,
            "_resolve_thread_context",
            return_value=(
                "thread_x",
                cli_module.RunMetadata(workspace_path="/tmp/workspace", session_key=None),
                {"workspaceId": "workspace_x"},
            ),
        ), mock.patch.object(
            cli_module,
            "_execute_agent_turn",
            return_value={
                "runtimeEvents": runtime_events,
                "result": {"final_answer": "Hello", "deltas": [], "session_messages": []},
                "finalAnswer": "Hello",
            },
        ), mock.patch.object(
            cli_module,
            "_build_session_service",
            return_value=(_FakeDb(), object(), _FakeSessionService()),
        ), mock.patch.object(
            cli_module.uuid,
            "uuid4",
            return_value=mock.Mock(hex="1234567890abcdef"),
        ):
            code, stdout, stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "agent",
                    "run",
                    "--prompt",
                    "hello",
                    "--json",
                    "--include-deprecated-timeline",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["timeline"], [
            {
                "offset": 1,
                "run_id": run_id,
                "thread_id": "thread_x",
                "event_type": "turn.started",
                "payload": {"type": "turn.started"},
                "created_at": "2026-01-01T00:00:00Z",
            }
        ])
        self.assertIn("deprecated", payload)
        self.assertIn("timeline", payload["deprecated"])
        self.assertIn("Use events", payload["deprecated"]["timeline"])

    def test_agent_run_json_failure_returns_turn_failed_event(self) -> None:
        class _FakeDb:
            def close(self) -> None:
                return None

        class _FakeSessionService:
            def list_timeline_payload(self, _session_id: str) -> dict[str, object]:
                return {"events": []}

        with mock.patch.object(
            cli_module,
            "_resolve_run_target",
            return_value=(
                object(),
                {"mode": "default-provider"},
                "provider_x",
                "model_x",
            ),
        ), mock.patch.object(
            cli_module,
            "_resolve_thread_context",
            return_value=(
                "thread_x",
                cli_module.RunMetadata(workspace_path="/tmp/workspace", session_key=None),
                {"workspaceId": "workspace_x"},
            ),
        ), mock.patch.object(
            cli_module,
            "_execute_agent_turn",
            side_effect=cli_module._CliTurnExecutionError(
                "runtime exploded",
                runtime_events=[
                    {
                        "type": "runtime_step",
                        "payload": {
                            "step_kind": "planning",
                            "step_id": "step-1",
                            "content_text": "1. Try task",
                        },
                    }
                ],
            ),
        ), mock.patch.object(
            cli_module,
            "_build_session_service",
            return_value=(_FakeDb(), object(), _FakeSessionService()),
        ):
            code, stdout, stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "agent",
                    "run",
                    "--prompt",
                    "hello",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertEqual(payload["error"], "runtime exploded")
        self.assertEqual(payload["events"][-1]["type"], "turn.failed")
        self.assertEqual(payload["events"][-1]["error"]["message"], "runtime exploded")
        todo_started = next(
            event
            for event in payload["events"]
            if event["type"] == "item.started" and event["item"]["type"] == "todo_list"
        )
        self.assertEqual(todo_started["item"]["items"][0]["text"], "Try task")

    def test_models_enable_is_removed(self) -> None:
        code, stdout, stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "models",
                "enable",
                "--provider-id",
                "openai",
                "--model",
                "gpt-5",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("No such command 'enable'", stderr)

    def test_agent_run_rejects_removed_provider_option(self) -> None:
        code, stdout, stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "agent",
                "run",
                "--prompt",
                "hello",
                "--provider-id",
                "openai",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("No such option: --provider-id", stderr)

    def test_agent_run_help_uses_thread_workspace_model_flags(self) -> None:
        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "agent",
                "run",
                "--help",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("--thread-id", stdout)
        self.assertIn("--workspace", stdout)
        self.assertIn("--model", stdout)
        self.assertIn("--background", stdout)
        self.assertIn("--json", stdout)
        self.assertIn("--include-deprecated-timeli", stdout)
        self.assertIn("--include-timeline` is kept", stdout)
        self.assertIn("--db-path", stdout)
        self.assertNotIn("--turn-id", stdout)
        self.assertNotIn("--workspace-path", stdout)
        self.assertNotIn("--provider-id", stdout)

    def test_agent_worker_help_uses_run_id_flag(self) -> None:
        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "agent",
                "worker",
                "--help",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("--run-id", stdout)
        self.assertNotIn("--thread-id", stdout)

    def test_agent_cancel_help_uses_run_id_flag(self) -> None:
        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "agent",
                "cancel",
                "--help",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("--run-id", stdout)
        self.assertNotIn("--thread-id", stdout)

    def test_agent_watch_help_exposes_json_flag(self) -> None:
        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "agent",
                "watch",
                "--help",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("--json", stdout)

    def test_models_help_exposes_db_path_and_json_flags(self) -> None:
        commands = ["list", "create", "get", "set-default"]
        for subcommand in commands:
            code, stdout, _stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "models",
                    subcommand,
                    "--help",
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("--db-path", stdout)
            self.assertIn("--json", stdout)

    def test_agent_cancel_prints_canceled(self) -> None:
        with mock.patch.object(
            cli_module,
            "_read_run_record",
            return_value={"thread_id": "thread_x"},
        ), mock.patch.object(cli_module, "_update_run_record", return_value={}):
            code, stdout, stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "agent",
                    "cancel",
                    "--run-id",
                    "run_x",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "Canceled")
        self.assertEqual(stderr, "")

    def test_watch_json_outputs_thread_event_rows_array(self) -> None:
        class _FakeDb:
            def close(self) -> None:
                return None

        class _FakeSessionService:
            def list_timeline_payload(self, session_id: str):
                self.last_session_id = session_id
                return {
                    "events": [
                        {
                            "sequenceNo": 1,
                            "turnId": "run_1",
                            "eventType": "acp.event",
                            "payload": {"type": "turn.started"},
                            "createdAt": "2026-01-01T00:00:00Z",
                        }
                    ]
                }

        fake_service = _FakeSessionService()
        with mock.patch.object(
            cli_module,
            "_build_session_service",
            return_value=(_FakeDb(), object(), fake_service),
        ):
            code, stdout, stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "agent",
                    "watch",
                    "--thread-id",
                    "thread_1",
                    "--from-offset",
                    "0",
                    "--no-follow",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        payload = json.loads(stdout)
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["run_id"], "run_1")
        self.assertEqual(payload[0]["thread_id"], "thread_1")
        self.assertEqual(payload[0]["event_type"], "turn.started")

    def test_root_acp_mode_routes_to_acp_stdio_bootstrap(self) -> None:
        with mock.patch("nsbot_sidecar.api.acp_stdio.main", return_value=17) as acp_main:
            code, stdout, stderr = _run_cli([
                "--ns-bot-home",
                self.temp_dir,
                "--acp",
            ])

        self.assertEqual(code, 17)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        acp_main.assert_called_once()
        config = acp_main.call_args.kwargs["config"]
        self.assertEqual(config.ns_bot_home, self.temp_dir)

    def test_help_short_option_supported_for_root(self) -> None:
        code, stdout, stderr = _run_cli(["-h"])

        self.assertEqual(code, 0)
        self.assertIn("Usage:", stdout)
        self.assertEqual(stderr, "")

    def test_help_short_option_supported_for_subcommand(self) -> None:
        code, stdout, stderr = _run_cli(["agent", "-h"])

        self.assertEqual(code, 0)
        self.assertIn("Usage:", stdout)
        self.assertEqual(stderr, "")

    def test_root_acp_mode_rejects_subcommands(self) -> None:
        with mock.patch("nsbot_sidecar.api.acp_stdio.main") as acp_main:
            code, stdout, stderr = _run_cli([
                "--ns-bot-home",
                self.temp_dir,
                "--acp",
                "agent",
                "run",
                "--prompt",
                "hello",
            ])

        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("ACP mode cannot be combined with subcommands", stderr)
        acp_main.assert_not_called()

    def test_agent_run_help_does_not_route_to_acp_stdio_bootstrap(self) -> None:
        with mock.patch("nsbot_sidecar.api.acp_stdio.main") as acp_main:
            code, stdout, _stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "agent",
                    "run",
                    "--help",
                ]
            )

        self.assertEqual(code, 0)
        self.assertIn("--thread-id", stdout)
        acp_main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
