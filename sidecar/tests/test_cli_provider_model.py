from __future__ import annotations

import io
import json
import subprocess
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
from pathlib import Path

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
        self.assertEqual(payload["runtime"]["hasApiKey"], True)

    def test_run_diagnose_uses_fd_rg_from_env_when_flags_absent(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "NSBOT_FD_EXECUTABLE": "/opt/tools/fd",
                "NSBOT_RG_EXECUTABLE": "/opt/tools/rg",
            },
            clear=False,
        ):
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
        self.assertEqual(payload["runtime"]["fdExecutable"], "/opt/tools/fd")
        self.assertEqual(payload["runtime"]["rgExecutable"], "/opt/tools/rg")

    def test_init_creates_ns_bot_home_and_copies_resources_from_cache(self) -> None:
        runtime_root = Path(tempfile.mkdtemp(prefix="sidecar-runtime-"))
        try:
            templates_dir = runtime_root / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            (templates_dir / "TOOLS.md").write_text("tool template", encoding="utf-8")

            cache_root = runtime_root / "vendor" / "search-tools"
            target = "aarch64-apple-darwin"
            fd_cache = cache_root / target / "fd" / "fd"
            rg_cache = cache_root / target / "rg" / "rg"
            fd_cache.parent.mkdir(parents=True, exist_ok=True)
            rg_cache.parent.mkdir(parents=True, exist_ok=True)
            fd_cache.write_text("fake-fd", encoding="utf-8")
            rg_cache.write_text("fake-rg", encoding="utf-8")

            ns_home = Path(self.temp_dir) / "bootstrap"
            with mock.patch("cli._runtime_paths", return_value={
                "repo_root": runtime_root,
                "sidecar_root": runtime_root,
                "templates_dir": templates_dir,
                "search_tools_cache_root": cache_root,
                "prepare_search_tools_script": runtime_root / "scripts" / "prepare_search_tools.py",
            }), mock.patch("cli._resolve_target_triple", return_value=target):
                code, stdout, _stderr = _run_cli(
                    ["--ns-bot-home", str(ns_home), "init"]
                )

            self.assertEqual(code, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["ok"], True)
            self.assertTrue((ns_home / "bin").exists())
            self.assertTrue((ns_home / "templates" / "TOOLS.md").exists())
            self.assertTrue((ns_home / "bin" / "fd").exists())
            self.assertTrue((ns_home / "bin" / "rg").exists())
            self.assertEqual(payload["prepared"]["templatesCopied"], True)
            self.assertEqual(payload["prepared"]["searchToolsCopied"], True)
            self.assertEqual(payload["prepared"]["searchToolsDownloaded"], False)
        finally:
            shutil.rmtree(runtime_root, ignore_errors=True)

    def test_init_skips_templates_copy_when_already_present(self) -> None:
        runtime_root = Path(tempfile.mkdtemp(prefix="sidecar-runtime-"))
        try:
            templates_dir = runtime_root / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            (templates_dir / "TOOLS.md").write_text("tool template", encoding="utf-8")

            cache_root = runtime_root / "vendor" / "search-tools"
            target = "aarch64-apple-darwin"
            for tool in ("fd", "rg"):
                tool_path = cache_root / target / tool / tool
                tool_path.parent.mkdir(parents=True, exist_ok=True)
                tool_path.write_text(f"fake-{tool}", encoding="utf-8")

            ns_home = Path(self.temp_dir) / "bootstrap2"
            (ns_home / "templates").mkdir(parents=True, exist_ok=True)
            (ns_home / "templates" / "TOOLS.md").write_text("existing", encoding="utf-8")

            with mock.patch("cli._runtime_paths", return_value={
                "repo_root": runtime_root,
                "sidecar_root": runtime_root,
                "templates_dir": templates_dir,
                "search_tools_cache_root": cache_root,
                "prepare_search_tools_script": runtime_root / "scripts" / "prepare_search_tools.py",
            }), mock.patch("cli._resolve_target_triple", return_value=target):
                code, stdout, _stderr = _run_cli(
                    ["--ns-bot-home", str(ns_home), "init"]
                )

            self.assertEqual(code, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["prepared"]["templatesCopied"], False)
            self.assertEqual(
                (ns_home / "templates" / "TOOLS.md").read_text(encoding="utf-8"),
                "existing",
            )
        finally:
            shutil.rmtree(runtime_root, ignore_errors=True)

    def test_init_downloads_search_tools_when_cache_missing(self) -> None:
        runtime_root = Path(tempfile.mkdtemp(prefix="sidecar-runtime-"))
        try:
            templates_dir = runtime_root / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            (templates_dir / "TOOLS.md").write_text("tool template", encoding="utf-8")

            cache_root = runtime_root / "vendor" / "search-tools"
            target = "aarch64-apple-darwin"
            script_path = runtime_root / "scripts" / "prepare_search_tools.py"
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text("# placeholder", encoding="utf-8")
            ns_home = Path(self.temp_dir) / "bootstrap3"

            def fake_run(*_args, **_kwargs):
                fd_cache = cache_root / target / "fd" / "fd"
                rg_cache = cache_root / target / "rg" / "rg"
                fd_cache.parent.mkdir(parents=True, exist_ok=True)
                rg_cache.parent.mkdir(parents=True, exist_ok=True)
                fd_cache.write_text("fake-fd", encoding="utf-8")
                rg_cache.write_text("fake-rg", encoding="utf-8")
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

            with mock.patch("cli._runtime_paths", return_value={
                "repo_root": runtime_root,
                "sidecar_root": runtime_root,
                "templates_dir": templates_dir,
                "search_tools_cache_root": cache_root,
                "prepare_search_tools_script": script_path,
            }), mock.patch("cli._resolve_target_triple", return_value=target), mock.patch("cli.subprocess.run", side_effect=fake_run):
                code, stdout, _stderr = _run_cli(
                    ["--ns-bot-home", str(ns_home), "init"]
                )

            self.assertEqual(code, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["prepared"]["searchToolsDownloaded"], True)
            self.assertTrue((ns_home / "bin" / "fd").exists())
            self.assertTrue((ns_home / "bin" / "rg").exists())
        finally:
            shutil.rmtree(runtime_root, ignore_errors=True)

    def test_init_returns_error_when_prepare_search_tools_fails(self) -> None:
        runtime_root = Path(tempfile.mkdtemp(prefix="sidecar-runtime-"))
        try:
            templates_dir = runtime_root / "templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            (templates_dir / "TOOLS.md").write_text("tool template", encoding="utf-8")

            cache_root = runtime_root / "vendor" / "search-tools"
            target = "aarch64-apple-darwin"
            script_path = runtime_root / "scripts" / "prepare_search_tools.py"
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text("# placeholder", encoding="utf-8")
            ns_home = Path(self.temp_dir) / "bootstrap4"

            with mock.patch("cli._runtime_paths", return_value={
                "repo_root": runtime_root,
                "sidecar_root": runtime_root,
                "templates_dir": templates_dir,
                "search_tools_cache_root": cache_root,
                "prepare_search_tools_script": script_path,
            }), mock.patch("cli._resolve_target_triple", return_value=target), mock.patch(
                "cli.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=1,
                    stdout="",
                    stderr="download failed",
                ),
            ):
                code, _stdout, stderr = _run_cli(
                    ["--ns-bot-home", str(ns_home), "init"]
                )

            self.assertEqual(code, 1)
            self.assertIn("download failed", stderr)
        finally:
            shutil.rmtree(runtime_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
