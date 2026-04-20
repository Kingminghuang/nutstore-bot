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
