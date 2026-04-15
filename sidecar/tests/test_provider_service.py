from __future__ import annotations

import shutil
import tempfile
import unittest

from nsbot_sidecar.application.provider_service import ProviderService
from nsbot_sidecar.infrastructure.repositories import create_repositories
from nsbot_sidecar.infrastructure.secret_store import LocalSecretStore
from nsbot_sidecar.infrastructure.storage import connect_database
from nsbot_sidecar.providers.provider_catalog import list_providers


class ProviderServiceModelOptionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="provider-service-")
        self.connection = connect_database(self.temp_dir)
        self.repositories = create_repositories(self.connection)
        self.service = ProviderService(
            repositories=self.repositories.providers,
            secret_store=LocalSecretStore(self.temp_dir),
        )

    def tearDown(self) -> None:
        self.connection.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_model_options_include_saved_provider_without_connected_health(self) -> None:
        openai_model_id = str(
            next(
                provider for provider in list_providers() if str(provider.get("id") or "") == "openai"
            )["models"][0]["id"]
        )

        bundle = self.repositories.providers.save_bundle(
            connection_data={
                "kind": "builtin",
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "base_url": None,
                "secret_ref": "sec_test_openai",
                "api_key_configured": True,
                "model_policy": "all_catalog",
                "preferred_model_id": openai_model_id,
                "is_enabled": True,
            },
            models=[],
        )

        payload = self.service.model_options_payload()

        self.assertEqual(len(payload["groups"]), 1)
        self.assertEqual(payload["groups"][0]["connectionId"], bundle.connection.id)
        self.assertEqual(payload["groups"][0]["models"][0]["modelId"], openai_model_id)
        self.assertEqual(
            payload["defaultSelection"],
            {"connectionId": bundle.connection.id, "modelId": openai_model_id},
        )
