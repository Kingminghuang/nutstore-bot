from __future__ import annotations

import shutil
import tempfile
import unittest

from nsbot.application.provider_service import ProviderService
from nsbot.infrastructure.repositories import create_repositories
from nsbot.infrastructure.secret_store import LocalSecretStore
from nsbot.infrastructure.storage import connect_database
from nsbot.providers.provider_catalog import list_providers


class ProviderServiceModelOptionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="provider-service-")
        self.connection = connect_database(self.temp_dir)
        self.repositories = create_repositories(self.connection)
        self.service = ProviderService(
            repositories=self.repositories.providers,
            default_model_selection=self.repositories.default_model_selection,
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
            provider_data={
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "base_url": None,
                "secret_ref": "sec_test_openai",
                "preferred_model_id": openai_model_id,
            },
            models=[],
        )

        payload = self.service.model_options_payload()

        self.assertEqual(len(payload["groups"]), 1)
        self.assertEqual(payload["groups"][0]["providerId"], bundle.provider.id)
        self.assertEqual(payload["groups"][0]["models"][0]["modelId"], openai_model_id)
        self.assertEqual(
            payload["defaultSelection"],
            {"providerId": bundle.provider.id, "modelId": openai_model_id},
        )

    def test_global_default_selection_overrides_provider_preference(self) -> None:
        openai_model_id = str(
            next(
                provider for provider in list_providers() if str(provider.get("id") or "") == "openai"
            )["models"][0]["id"]
        )
        anthropic_model_id = str(
            next(
                provider
                for provider in list_providers()
                if str(provider.get("id") or "") == "anthropic"
            )["models"][0]["id"]
        )

        self.repositories.providers.save_bundle(
            provider_data={
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "base_url": None,
                "secret_ref": "sec_test_openai",
                "preferred_model_id": openai_model_id,
            },
            models=[],
        )
        anthropic_bundle = self.repositories.providers.save_bundle(
            provider_data={
                "runtime_provider": "anthropic",
                "catalog_provider_id": "anthropic",
                "display_name": "Anthropic",
                "base_url": None,
                "secret_ref": "sec_test_anthropic",
                "preferred_model_id": anthropic_model_id,
            },
            models=[],
        )
        self.repositories.default_model_selection.set(
            anthropic_bundle.provider.id,
            anthropic_model_id,
        )

        payload = self.service.model_options_payload()

        self.assertEqual(
            payload["defaultSelection"],
            {"providerId": anthropic_bundle.provider.id, "modelId": anthropic_model_id},
        )
