from __future__ import annotations

import unittest

from nsbot.providers.provider_catalog import (
    NUTSTORE_BASE_URL,
    catalog_version,
    list_providers,
)


class ProviderCatalogTests(unittest.TestCase):
    def test_provider_catalog_includes_ui_metadata(self) -> None:
        providers = list_providers()
        self.assertGreaterEqual(len(providers), 5)

        for provider in providers:
            self.assertEqual(provider["kind"], "builtin")
            self.assertIn("runtimeProvider", provider)
            self.assertIn("baseUrlPolicy", provider)

        gemini = next(provider for provider in providers if provider["id"] == "gemini")
        self.assertEqual(gemini["baseUrlPolicy"], "hidden")

        openai = next(provider for provider in providers if provider["id"] == "openai")
        self.assertEqual(openai["baseUrlPolicy"], "optional")

        nutstore = next(provider for provider in providers if provider["id"] == "nutstore")
        self.assertEqual(nutstore["runtimeProvider"], "openai")
        self.assertEqual(nutstore["baseUrlPolicy"], "hidden")
        self.assertEqual(nutstore["baseUrl"], NUTSTORE_BASE_URL)

        models_by_id = {
            str(model["id"]): model for model in nutstore.get("models", [])
        }
        self.assertIn("qwen/qwen3.6-plus", models_by_id)
        self.assertIn("openai/gpt-5.4", models_by_id)
        self.assertIn("google/gemini-3.1-pro-preview-customtools", models_by_id)
        self.assertIn("anthropic/claude-sonnet-4.6", models_by_id)

        self.assertEqual(
            models_by_id["moonshotai/kimi-k2.6"]["reasoningEffortValues"],
            ["enabled", "disabled"],
        )
        self.assertEqual(
            models_by_id["z-ai/glm-5.1"]["reasoningEffortValues"],
            ["enabled", "disabled"],
        )
        self.assertEqual(
            models_by_id["qwen/qwen3.6-plus"]["reasoningEffortValues"],
            ["enabled", "disabled"],
        )
        self.assertEqual(
            models_by_id["xiaomi/mimo-v2-pro"]["reasoningEffortValues"],
            ["enabled", "disabled"],
        )
        self.assertTrue(models_by_id["minimax/minimax-m2.7"]["supportsReasoningTokens"])
        self.assertNotIn("reasoningEffortValues", models_by_id["minimax/minimax-m2.7"])
        self.assertEqual(
            models_by_id["openai/gpt-5.4"]["reasoningEffortValues"],
            ["none", "low", "medium", "high", "xhigh"],
        )
        self.assertEqual(
            models_by_id["google/gemini-3.1-pro-preview-customtools"]["reasoningEffortValues"],
            ["low", "high"],
        )
        self.assertEqual(
            models_by_id["anthropic/claude-sonnet-4.6"]["reasoningEffortValues"],
            ["low", "medium", "high"],
        )

    def test_catalog_version_is_stable_for_same_payload(self) -> None:
        providers = list_providers()
        self.assertEqual(catalog_version(providers), catalog_version(list(providers)))
