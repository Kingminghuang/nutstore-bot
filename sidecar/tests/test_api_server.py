from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from python_runtime.api_server import (
    ApiServerConfig,
    create_app,
    publish_service_discovery,
)
from python_runtime.discovery import read_service_discovery


class ApiServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="sidecar-api-"))
        self.config = ApiServerConfig(
            host="127.0.0.1",
            port=8765,
            token="test-token",
            ns_bot_home=str(self.temp_dir),
        )
        self.client = TestClient(create_app(self.config))

    def test_health_is_public(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "service": "sidecar",
                "version": "0.1.0",
            },
        )

    def test_auth_check_requires_bearer_token(self) -> None:
        response = self.client.get("/auth/check")
        self.assertEqual(response.status_code, 401)

        response = self.client.get(
            "/auth/check",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_publish_service_discovery(self) -> None:
        path = publish_service_discovery(self.config, self.config.token)
        self.assertTrue(path.exists())

        discovery = read_service_discovery(str(self.temp_dir))
        self.assertEqual(discovery.base_url, "http://127.0.0.1:8765")
        self.assertEqual(discovery.port, 8765)
        self.assertEqual(discovery.token, "test-token")
        self.assertGreater(discovery.pid, 0)

    def test_invalid_host_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            create_app(ApiServerConfig(host="0.0.0.0", token="bad-token"))

    def test_provider_catalog_requires_auth_and_returns_custom_template(self) -> None:
        unauthorized = self.client.get("/provider-catalog")
        self.assertEqual(unauthorized.status_code, 401)

        response = self.client.get(
            "/provider-catalog",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("providers", body)
        self.assertTrue(any(item["id"] == "custom" for item in body["providers"]))

    def test_builtin_provider_persistence_redacts_api_key(self) -> None:
        response = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "modelPolicy": "all_catalog",
                "preferredModelId": "gpt-5.4",
                "enabledModelIds": ["gpt-5.4"],
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["kind"], "builtin")
        self.assertEqual(body["runtimeProvider"], "openai")
        self.assertEqual(body["apiKeyConfigured"], True)
        self.assertNotIn("apiKey", body)

        listing = self.client.get(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()["connections"]), 1)

    def test_custom_provider_requires_base_url(self) -> None:
        response = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "custom",
                "customSlug": "my-company",
                "displayName": "My Company Gateway",
                "apiKey": "sk-test",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_custom_provider_persistence_redacts_secret_headers(self) -> None:
        response = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "custom",
                "customSlug": "my-company",
                "displayName": "My Company Gateway",
                "baseUrl": "https://llm.example.com/v1",
                "apiKey": "sk-test",
                "customModels": [
                    {
                        "modelId": "my-model",
                        "displayName": "My Model",
                    }
                ],
                "headers": [
                    {
                        "name": "X-Tenant",
                        "valueKind": "plain",
                        "plainValue": "team-a",
                    },
                    {
                        "name": "X-Token",
                        "valueKind": "secret",
                        "secretValue": "secret-123",
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["kind"], "custom")
        self.assertEqual(body["baseUrl"], "https://llm.example.com/v1")
        self.assertEqual(len(body["customModels"]), 1)
        secret_headers = [
            header for header in body["headers"] if header["valueKind"] == "secret"
        ]
        self.assertEqual(len(secret_headers), 1)
        self.assertTrue(secret_headers[0]["hasStoredSecret"])

    def test_update_and_delete_provider(self) -> None:
        created = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "enabledModelIds": ["gpt-5.4"],
            },
        )
        provider_id = created.json()["id"]

        updated = self.client.patch(
            f"/providers/{provider_id}",
            headers={"Authorization": "Bearer test-token"},
            json={
                "displayName": "OpenAI Primary",
                "preferredModelId": "gpt-5.4",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["displayName"], "OpenAI Primary")
        self.assertEqual(updated.json()["apiKeyConfigured"], True)

        deleted = self.client.delete(
            f"/providers/{provider_id}",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(deleted.status_code, 204)

        listing = self.client.get(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["connections"], [])


if __name__ == "__main__":
    unittest.main()
