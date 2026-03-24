from __future__ import annotations

import shutil
import tempfile
import unittest

from python_runtime.secret_store import LocalSecretStore, ProviderSecretPayload


class LocalSecretStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sidecar-secret-store-")
        self.store = LocalSecretStore(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_bootstrap_and_round_trip_secret(self) -> None:
        master_key_path = self.store.bootstrap_master_key()
        self.assertTrue(master_key_path.endswith("master.key"))

        secret_path = self.store.save_provider_secret(
            "sec_provider_1",
            ProviderSecretPayload(
                version=1,
                api_key="sk-test",
                secret_headers={"hdr_1": "secret-value"},
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
                secret_headers={"hdr_1": "secret-value"},
            ),
        )

        self.store.delete_provider_secret("sec_provider_1")
        self.assertFalse(self.store.has_secret("sec_provider_1"))
        self.assertIsNone(self.store.load_provider_secret("sec_provider_1"))

    def test_overwriting_secret_payload_removes_stale_secret_headers(self) -> None:
        self.store.save_provider_secret(
            "sec_provider_2",
            ProviderSecretPayload(
                version=1,
                api_key="sk-old",
                secret_headers={"hdr_1": "secret-a", "hdr_2": "secret-b"},
            ),
        )

        self.store.save_provider_secret(
            "sec_provider_2",
            ProviderSecretPayload(
                version=1,
                api_key="sk-new",
                secret_headers={"hdr_2": "secret-b-rotated"},
            ),
        )

        payload = self.store.load_provider_secret("sec_provider_2")
        self.assertEqual(payload.api_key, "sk-new")
        self.assertEqual(payload.secret_headers, {"hdr_2": "secret-b-rotated"})
