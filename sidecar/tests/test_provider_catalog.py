from __future__ import annotations

import unittest

from python_runtime.provider_catalog import list_providers


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
