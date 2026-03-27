from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cli import main as cli_main
from repositories import create_repositories
from secret_store import LocalSecretStore
from storage import connect_database


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli_main(argv)
    return code, out.getvalue(), err.getvalue()


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> dict[str, object]:
        return self._payload


class CliAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sidecar-auth-")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_auth_login_no_listen_generates_pending(self) -> None:
        code, stdout, _stderr = _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "auth",
                "login",
                "--gateway-base-url",
                "https://gateway.example.com",
                "--no-open",
                "--no-listen",
            ]
        )

        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["status"], "pending")
        self.assertTrue(
            payload["authorizeUrl"].startswith(
                "https://gateway.example.com/d/openid/auth?"
            )
        )

        pending_path = Path(self.temp_dir) / "auth-nutstore-pending.json"
        self.assertTrue(pending_path.exists())
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
        self.assertEqual(pending["gatewayBaseUrl"], "https://gateway.example.com")
        self.assertEqual(pending["provider"], "nutstore")
        self.assertIn("state", pending)
        self.assertIn("nonce", pending)

    def test_auth_paste_redirect_exchanges_and_persists_provider(self) -> None:
        _run_cli(
            [
                "--ns-bot-home",
                self.temp_dir,
                "auth",
                "login",
                "--gateway-base-url",
                "https://gateway.example.com",
                "--model",
                "nutstore-pro",
                "--no-open",
                "--no-listen",
            ]
        )
        pending_path = Path(self.temp_dir) / "auth-nutstore-pending.json"
        pending = json.loads(pending_path.read_text(encoding="utf-8"))

        with patch(
            "cli.requests.post",
            return_value=_FakeResponse(
                {
                    "access_token": "gateway-token-123",
                    "token_type": "Bearer",
                    "expires_in": 1800,
                }
            ),
        ) as post_mock:
            code, stdout, _stderr = _run_cli(
                [
                    "--ns-bot-home",
                    self.temp_dir,
                    "auth",
                    "paste-redirect",
                    "--input",
                    f"http://localhost:1457/auth/callback?code=abc123&state={pending['state']}",
                ]
            )

        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["providerId"], "nutstore")
        self.assertEqual(payload["modelId"], "nutstore-pro")

        self.assertEqual(post_mock.call_count, 1)
        post_kwargs = post_mock.call_args.kwargs
        self.assertEqual(
            post_kwargs["json"],
            {
                "code": "abc123",
                "redirect_uri": pending["redirectUri"],
                "nonce": pending["nonce"],
            },
        )

        database = connect_database(self.temp_dir)
        repositories = create_repositories(database)
        secret_store = LocalSecretStore(self.temp_dir)
        try:
            bundles = repositories.providers.list_bundles()
            self.assertEqual(len(bundles), 1)
            bundle = bundles[0]
            self.assertEqual(bundle.connection.kind, "custom")
            self.assertEqual(bundle.connection.custom_slug, "nutstore")
            self.assertEqual(
                bundle.connection.base_url, "https://gateway.example.com/v1"
            )
            self.assertEqual(bundle.connection.preferred_model_id, "nutstore-pro")

            secret_payload = secret_store.load_provider_secret(
                bundle.connection.secret_ref
            )
            self.assertIsNotNone(secret_payload)
            self.assertEqual(secret_payload.api_key, "gateway-token-123")
        finally:
            database.close()

        self.assertFalse(pending_path.exists())


if __name__ == "__main__":
    unittest.main()
