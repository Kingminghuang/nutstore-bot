from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from nsbot_sidecar.infrastructure.secret_store import LocalSecretStore, ProviderSecretPayload


class LocalSecretStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sidecar-secret-store-")
        self.store = LocalSecretStore(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_bootstrap_and_round_trip_secret(self) -> None:
        master_key_path = self.store.bootstrap_master_key()
        self.assertEqual(master_key_path, "plaintext-mode: no master key file")

        secret_path = self.store.save_provider_secret(
            "sec_provider_1",
            ProviderSecretPayload(
                version=1,
                api_key="sk-test",
            ),
        )
        self.assertTrue(secret_path.endswith("sec_provider_1.enc"))
        self.assertTrue(self.store.has_secret("sec_provider_1"))

        payload = self.store.load_provider_secret("sec_provider_1")
        self.assertEqual(
            payload,
            ProviderSecretPayload(
                version=1,
                api_key="sk-test",
            ),
        )
        file_payload = json.loads(Path(secret_path).read_text(encoding="utf-8"))
        self.assertEqual(file_payload["version"], 1)
        self.assertEqual(file_payload["apiKey"], "sk-test")

        self.store.delete_provider_secret("sec_provider_1")
        self.assertFalse(self.store.has_secret("sec_provider_1"))
        self.assertIsNone(self.store.load_provider_secret("sec_provider_1"))

    def test_load_provider_secret_ignores_legacy_secret_headers_key(self) -> None:
        self.store.save_provider_secret(
            "sec_provider_2",
            ProviderSecretPayload(
                version=1,
                api_key="sk-old",
            ),
        )

        secret_path = Path(self.temp_dir) / "secrets" / "sec_provider_2.enc"
        secret_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "apiKey": "sk-new",
                    "secretHeaders": {"hdr_1": "legacy"},
                }
            ),
            encoding="utf-8",
        )

        payload = self.store.load_provider_secret("sec_provider_2")
        self.assertEqual(payload.api_key, "sk-new")
