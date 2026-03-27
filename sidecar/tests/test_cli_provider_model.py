from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from cli import main as cli_main
from provider_catalog import list_providers
from repositories import create_repositories
from storage import connect_database


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
            connection_data={
                "kind": "builtin",
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "base_url": None,
                "secret_ref": "sec_test_openai",
                "api_key_configured": True,
                "health_status": "connected",
                "health_message": "Validation succeeded",
                "last_validated_at": None,
                "model_policy": "all_catalog",
                "preferred_model_id": None,
                "is_enabled": True,
            },
            models=[],
            headers=[],
        )
        return bundle.connection.id

    def test_providers_use_auto_selects_first_model(self) -> None:
        connection_id = self._create_builtin_openai()
        openai_entry = next(
            item for item in list_providers() if str(item.get("id")) == "openai"
        )
        expected_model_id = str(openai_entry["models"][0]["id"])

        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "providers",
                "use",
                "openai",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["connectionId"], connection_id)
        self.assertEqual(payload["modelId"], expected_model_id)

        refreshed = self.repositories.providers.get_bundle_by_id_or_raise(connection_id)
        self.assertEqual(refreshed.connection.preferred_model_id, expected_model_id)

    def test_providers_delete_requires_connection_id(self) -> None:
        connection_id = self._create_builtin_openai()
        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "providers",
                "delete",
                "--connection-id",
                connection_id,
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["deletedConnectionId"], connection_id)
        self.assertIsNone(self.repositories.providers.get_bundle_by_id(connection_id))

    def test_models_remove_rejected_for_builtin_connection(self) -> None:
        connection_id = self._create_builtin_openai()
        code, _stdout, stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "models",
                "remove",
                "--connection-id",
                connection_id,
                "--model",
                "gpt-5.4",
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("only supported for custom", stderr)

    def test_models_remove_custom_deletes_model(self) -> None:
        bundle = self.repositories.providers.save_bundle(
            connection_data={
                "kind": "custom",
                "runtime_provider": "custom",
                "catalog_provider_id": None,
                "custom_slug": "my-gateway",
                "display_name": "My Gateway",
                "base_url": "https://llm.example.com/v1",
                "secret_ref": "sec_test_custom",
                "api_key_configured": True,
                "health_status": "connected",
                "health_message": "Validation succeeded",
                "last_validated_at": None,
                "model_policy": "custom_only",
                "preferred_model_id": "model-a",
                "is_enabled": True,
            },
            models=[
                {
                    "source": "custom",
                    "model_id": "model-a",
                    "display_name": "Model A",
                    "enabled": True,
                    "sort_order": 0,
                },
                {
                    "source": "custom",
                    "model_id": "model-b",
                    "display_name": "Model B",
                    "enabled": True,
                    "sort_order": 1,
                },
            ],
            headers=[],
        )

        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "models",
                "remove",
                "--connection-id",
                bundle.connection.id,
                "--model",
                "model-a",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["action"], "removed")

        refreshed = self.repositories.providers.get_bundle_by_id_or_raise(
            bundle.connection.id
        )
        self.assertEqual(
            [model.model_id for model in refreshed.models if model.source == "custom"],
            ["model-b"],
        )
        self.assertEqual(refreshed.connection.preferred_model_id, "model-b")

    def test_run_diagnose_uses_default_selection(self) -> None:
        connection_id = self._create_builtin_openai()
        openai_entry = next(
            item for item in list_providers() if str(item.get("id") or "") == "openai"
        )
        expected_model_id = str(openai_entry["models"][0]["id"])

        _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "providers",
                "use",
                "openai",
            ]
        )

        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "run",
                "diagnose test",
                "--diagnose",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["resolved"]["mode"], "default-selection")
        self.assertEqual(payload["resolved"]["connectionId"], connection_id)
        self.assertEqual(payload["resolved"]["modelId"], expected_model_id)
        self.assertEqual(payload["runtime"]["provider"], "openai")

    def test_run_diagnose_direct_mode(self) -> None:
        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "run",
                "diagnose direct",
                "--diagnose",
                "--provider",
                "custom",
                "--base-url",
                "https://llm.example.com/v1",
                "--api-key",
                "sk-direct",
                "--model",
                "demo-direct-model",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["resolved"]["mode"], "direct")
        self.assertEqual(payload["resolved"]["runtimeProvider"], "custom")
        self.assertEqual(payload["resolved"]["modelId"], "demo-direct-model")
        self.assertEqual(payload["runtime"]["hasDirectApiKey"], True)


if __name__ == "__main__":
    unittest.main()
