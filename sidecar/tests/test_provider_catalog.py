from __future__ import annotations

import unittest

from nsbot_sidecar.providers.provider_catalog import catalog_version, list_providers


class ProviderCatalogTests(unittest.TestCase):
    def test_provider_catalog_includes_ui_metadata(self) -> None:
        providers = list_providers()
        self.assertGreaterEqual(len(providers), 4)

        for provider in providers:
            self.assertEqual(provider["kind"], "builtin")
            self.assertIn("runtimeProvider", provider)
            self.assertIn("baseUrlPolicy", provider)

        gemini = next(provider for provider in providers if provider["id"] == "gemini")
        self.assertEqual(gemini["baseUrlPolicy"], "hidden")

        openai = next(provider for provider in providers if provider["id"] == "openai")
        self.assertEqual(openai["baseUrlPolicy"], "optional")

    def test_catalog_version_is_stable_for_same_payload(self) -> None:
        providers = list_providers()
        self.assertEqual(catalog_version(providers), catalog_version(list(providers)))
