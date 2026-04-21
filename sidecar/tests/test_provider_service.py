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

    def test_add_model_creates_builtin_provider_from_catalog_match(self) -> None:
        bundle = self.service.add_model(
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model_id="gpt-5.4",
            provider_display_name="OpenAI Override",
            model_display_name="Ignored for builtin",
        )

        self.assertEqual(bundle["kind"], "builtin")
        self.assertEqual(bundle["id"], "openai")
        self.assertEqual(bundle["catalogProviderId"], "openai")
        self.assertEqual(bundle["displayName"], "OpenAI Override")
        self.assertEqual(bundle["preferredModelId"], "gpt-5.4")
        self.assertEqual(bundle["customModels"], [])

    def test_add_model_creates_builtin_provider_from_exact_catalog_model_id(self) -> None:
        bundle = self.service.add_model(
            base_url="https://api.deepseek.com",
            api_key="sk-test",
            model_id="deepseek/deepseek-chat",
            provider_display_name="Deepseek Override",
            model_display_name="Ignored for builtin",
        )

        self.assertEqual(bundle["kind"], "builtin")
        self.assertEqual(bundle["id"], "deepseek")
        self.assertEqual(bundle["catalogProviderId"], "deepseek")
        self.assertEqual(bundle["preferredModelId"], "deepseek/deepseek-chat")

    def test_add_model_falls_back_to_custom_for_openai_prefixed_model_id(self) -> None:
        bundle = self.service.add_model(
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model_id="openai/gpt-5.4",
            provider_display_name="OpenAI Compat",
            model_display_name="OpenAI Compat",
        )

        self.assertEqual(bundle["kind"], "custom")
        self.assertEqual(bundle["id"], "openai/gpt-5.4")
        self.assertEqual(bundle["customSlug"], "openai/gpt-5.4")
        self.assertEqual(bundle["preferredModelId"], "openai/gpt-5.4")
        self.assertEqual(bundle["customModels"][0]["modelId"], "openai/gpt-5.4")

    def test_add_model_falls_back_to_custom_provider_id_from_model(self) -> None:
        bundle = self.service.add_model(
            base_url="https://llm.example.com/v1",
            api_key="sk-test",
            model_id="model-a",
            provider_display_name="Display Only",
            model_display_name="Custom Model A",
        )

        self.assertEqual(bundle["kind"], "custom")
        self.assertEqual(bundle["id"], "model-a")
        self.assertEqual(bundle["customSlug"], "model-a")
        self.assertEqual(bundle["displayName"], "model-a")
        self.assertEqual(bundle["preferredModelId"], "model-a")
        self.assertEqual(bundle["customModels"][0]["displayName"], "Custom Model A")

    def test_add_model_updates_existing_builtin_provider(self) -> None:
        self.repositories.providers.save_bundle(
            provider_data={
                "id": "openai",
                "runtime_provider": "openai",
                "catalog_provider_id": "openai",
                "display_name": "OpenAI",
                "base_url": None,
                "secret_ref": "sec_openai",
                "preferred_model_id": "gpt-5.2",
            },
            models=[],
        )

        bundle = self.service.add_model(
            base_url="https://api.openai.com/v1",
            api_key="sk-test-updated",
            model_id="gpt-5.4",
            provider_display_name="OpenAI Updated",
            model_display_name="Ignored for builtin",
        )

        self.assertEqual(bundle["kind"], "builtin")
        self.assertEqual(bundle["id"], "openai")
        self.assertEqual(bundle["preferredModelId"], "gpt-5.4")
        self.assertEqual(bundle["displayName"], "OpenAI Updated")

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
