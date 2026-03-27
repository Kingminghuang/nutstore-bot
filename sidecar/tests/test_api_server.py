from __future__ import annotations

from dataclasses import replace
import logging
import tempfile
import unittest
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
from fastapi import FastAPI, HTTPException
from smolagents.models import ChatMessageStreamDelta, Model
from smolagents.monitoring import TokenUsage

from python_runtime.api_server import (
    ApiServerConfig,
    create_app,
    publish_service_discovery,
)
from python_runtime.direct_model import DirectModelError


class FakeValidationSuccessModel(Model):
    def __init__(self) -> None:
        super().__init__(model_id="fake-validation")

    def generate_stream(
        self,
        messages,
        stop_sequences=None,
        response_format=None,
        tools_to_call_from=None,
        **kwargs,
    ):
        yield ChatMessageStreamDelta(
            content="OK",
            token_usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


class FakeValidationFailureModel(Model):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(model_id="fake-validation")
        self.code = code
        self.message = message

    def generate_stream(
        self,
        messages,
        stop_sequences=None,
        response_format=None,
        tools_to_call_from=None,
        **kwargs,
    ):
        raise DirectModelError(self.code, self.message)


from python_runtime.discovery import read_service_discovery
from python_runtime.local_paths import nsbot_home
from python_runtime.runtime_service import (
    RunMetadata,
    RuntimeProcessError,
    RuntimeWorkerConfig,
)


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

    @property
    def app(self) -> FastAPI:
        return cast(FastAPI, self.client.app)

    def _set_sync_run_launcher(self) -> None:
        self.app.state.run_service = replace(
            self.app.state.run_service,
            run_launcher=lambda task: task(),
        )

    def _set_validation_model_factory(self, factory) -> None:
        object.__setattr__(self.app.state.provider_service, "model_factory", factory)

    def _validate_provider(
        self, provider_id: str, model_id: str | None = None
    ) -> dict[str, object]:
        payload = {"modelId": model_id} if model_id is not None else {}
        response = self.client.post(
            f"/providers/{provider_id}/validate",
            headers={"Authorization": "Bearer test-token"},
            json=payload,
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _create_workspace(self, name: str = "workspace") -> dict[str, str]:
        workspace_dir = self.temp_dir / name
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return self.client.post(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": name,
                "realPath": str(workspace_dir),
                "pathLabel": str(workspace_dir),
            },
        ).json()

    def _create_provider(self) -> dict[str, object]:
        return self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "preferredModelId": "gpt-5.4",
            },
        ).json()

    def _create_custom_provider(self) -> dict[str, object]:
        return self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "custom",
                "customSlug": "team-gateway",
                "displayName": "Team Gateway",
                "baseUrl": "https://llm.example.com/v1",
                "apiKey": "sk-custom-test",
                "preferredModelId": "team-model",
                "customModels": [
                    {
                        "modelId": "team-model",
                        "displayName": "Team Model",
                    }
                ],
            },
        ).json()

    def _create_session(
        self, workspace_id: str, connection_id: str
    ) -> dict[str, object]:
        return self.client.post(
            f"/workspaces/{workspace_id}/sessions",
            headers={"Authorization": "Bearer test-token"},
            json={
                "connectionId": connection_id,
                "modelId": "gpt-5.4",
            },
        ).json()

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
        self.assertIn("version", body)
        self.assertIn("providers", body)
        self.assertTrue(any(item["id"] == "custom" for item in body["providers"]))

    def test_request_validation_error_redacts_raw_input(self) -> None:
        response = self.client.post(
            "/providers",
            headers={
                "Authorization": "Bearer test-token",
                "Content-Type": "text/plain",
            },
            content='{"kind":"custom","apiKey":"sk-sensitive"}',
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertIn("detail", body)
        self.assertIsInstance(body["detail"], list)
        self.assertGreater(len(body["detail"]), 0)
        first_error = body["detail"][0]
        self.assertNotIn("input", first_error)
        self.assertEqual(first_error.get("loc"), ["body"])

    def test_http_exception_detail_redacts_sensitive_values(self) -> None:
        @self.app.get("/_redaction-http-exception")
        def _redaction_http_exception() -> None:
            raise HTTPException(
                status_code=400,
                detail='invalid payload: {"apiKey":"sk-sensitive"}',
            )

        response = self.client.get(
            "/_redaction-http-exception",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 400)
        detail = response.json().get("detail", "")
        self.assertNotIn("sk-sensitive", detail)
        self.assertIn("[REDACTED]", detail)

    def test_provider_save_blocks_sensitive_data_in_non_secret_fields(self) -> None:
        with self.assertLogs(
            "provider_service", level=logging.WARNING
        ) as captured_logs:
            response = self.client.post(
                "/providers",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "kind": "custom",
                    "customSlug": "unsafe-provider",
                    "displayName": 'unsafe apiKey="sk-sensitive-value"',
                    "baseUrl": "https://llm.example.com/v1",
                    "apiKey": "sk-safe-channel",
                    "preferredModelId": "safe-model",
                    "customModels": [
                        {
                            "modelId": "safe-model",
                            "displayName": "Safe Model",
                            "enabled": True,
                        }
                    ],
                    "headers": [],
                },
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(
            body.get("detail"),
            "Sensitive data detected in non-secret persisted fields",
        )
        self.assertNotIn("sk-sensitive-value", "\n".join(captured_logs.output))
        self.assertIn("[REDACTED]", "\n".join(captured_logs.output))

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

    def test_builtin_openai_provider_can_be_saved_without_base_url(self) -> None:
        response = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "baseUrl": None,
                "apiKey": "sk-test",
                "preferredModelId": "gpt-5.4",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNone(body["baseUrl"])
        self.assertEqual(body["runtimeProvider"], "openai")

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

        provider_id = body["id"]

        rotated = self.client.patch(
            f"/providers/{provider_id}",
            headers={"Authorization": "Bearer test-token"},
            json={
                "apiKey": "sk-rotated",
                "headers": [
                    {
                        "id": secret_headers[0]["id"],
                        "name": "X-Token",
                        "valueKind": "plain",
                        "plainValue": "now-plain",
                    }
                ],
            },
        )
        self.assertEqual(rotated.status_code, 200)

        relisted = self.client.get(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
        )
        listed_headers = relisted.json()["connections"][0]["headers"]
        self.assertEqual(len(listed_headers), 1)
        self.assertEqual(listed_headers[0]["valueKind"], "plain")
        self.assertEqual(listed_headers[0]["valuePreview"], "now-plain")
        self.assertNotIn("hasStoredSecret", listed_headers[0])

    def test_validate_provider_returns_ok_for_builtin_openai(self) -> None:
        self._set_validation_model_factory(lambda config: FakeValidationSuccessModel())
        created = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "preferredModelId": "gpt-5.4",
            },
        )
        provider_id = created.json()["id"]

        response = self.client.post(
            f"/providers/{provider_id}/validate",
            headers={"Authorization": "Bearer test-token"},
            json={},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "providerId": provider_id,
                "modelId": "gpt-5.4",
                "runtimeProvider": "openai",
                "baseUrl": None,
                "healthStatus": "connected",
                "healthMessage": "Validation succeeded",
                "lastValidatedAt": response.json()["lastValidatedAt"],
            },
        )
        self.assertIsNotNone(response.json()["lastValidatedAt"])

        listing = self.client.get(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["connections"][0]["healthStatus"], "connected")

    def test_validate_provider_reports_missing_api_key(self) -> None:
        self._set_validation_model_factory(lambda config: FakeValidationSuccessModel())
        created = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "custom",
                "customSlug": "missing-key",
                "displayName": "Missing Key Provider",
                "baseUrl": "https://llm.example.com/v1",
                "customModels": [{"modelId": "my-model", "displayName": "My Model"}],
            },
        )
        provider_id = created.json()["id"]

        response = self.client.post(
            f"/providers/{provider_id}/validate",
            headers={"Authorization": "Bearer test-token"},
            json={"modelId": "my-model"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], False)
        self.assertEqual(response.json()["errorCode"], "missing_api_key")
        self.assertEqual(response.json()["healthStatus"], "invalid_config")

    def test_validate_provider_uses_requested_model_id(self) -> None:
        captured_model_ids: list[str] = []

        def model_factory(config) -> FakeValidationSuccessModel:
            captured_model_ids.append(config.model_id)
            return FakeValidationSuccessModel()

        self._set_validation_model_factory(model_factory)
        created = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "custom",
                "customSlug": "team-gateway-validate",
                "displayName": "Team Gateway",
                "baseUrl": "https://llm.example.com/v1",
                "apiKey": "sk-custom-test",
                "preferredModelId": "team-model",
                "customModels": [
                    {"modelId": "team-model", "displayName": "Team Model"},
                    {"modelId": "team-model-v2", "displayName": "Team Model V2"},
                ],
            },
        )
        provider_id = created.json()["id"]

        response = self.client.post(
            f"/providers/{provider_id}/validate",
            headers={"Authorization": "Bearer test-token"},
            json={"modelId": "team-model-v2"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)
        self.assertEqual(response.json()["modelId"], "team-model-v2")
        self.assertEqual(response.json()["healthStatus"], "connected")
        self.assertEqual(captured_model_ids, ["team-model-v2"])

    def test_validate_provider_persists_failure_status_from_probe(self) -> None:
        self._set_validation_model_factory(
            lambda config: FakeValidationFailureModel("unauthorized", "Invalid API key")
        )
        created = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "preferredModelId": "gpt-5.4",
            },
        )
        provider_id = created.json()["id"]

        response = self.client.post(
            f"/providers/{provider_id}/validate",
            headers={"Authorization": "Bearer test-token"},
            json={},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], False)
        self.assertEqual(response.json()["healthStatus"], "invalid_key")
        self.assertEqual(response.json()["healthMessage"], "Invalid API key")

        listing = self.client.get(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(
            listing.json()["connections"][0]["healthStatus"], "invalid_key"
        )

    def test_model_options_requires_auth(self) -> None:
        response = self.client.get("/model-options")
        self.assertEqual(response.status_code, 401)

    def test_model_options_returns_built_in_catalog_group_and_default(self) -> None:
        self._set_validation_model_factory(lambda config: FakeValidationSuccessModel())
        create_response = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "modelPolicy": "all_catalog",
                "preferredModelId": "gpt-5.4",
            },
        )
        self.assertEqual(create_response.status_code, 200)
        connection_id = create_response.json()["id"]
        self._validate_provider(connection_id)

        response = self.client.get(
            "/model-options",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(len(body["groups"]), 1)
        group = body["groups"][0]
        self.assertEqual(group["connectionId"], connection_id)
        self.assertEqual(group["providerLabel"], "OpenAI")
        self.assertEqual(group["providerId"], "openai")
        self.assertTrue(any(model["modelId"] == "gpt-5.4" for model in group["models"]))
        self.assertEqual(
            body["defaultSelection"],
            {"connectionId": connection_id, "modelId": "gpt-5.4"},
        )

    def test_model_options_respects_builtin_restricted_models(self) -> None:
        self._set_validation_model_factory(lambda config: FakeValidationSuccessModel())
        create_response = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "modelPolicy": "restricted",
                "enabledModelIds": ["gpt-5.4-mini", "gpt-5.4"],
                "preferredModelId": "gpt-5.4-mini",
            },
        )
        self.assertEqual(create_response.status_code, 200)
        self._validate_provider(create_response.json()["id"])

        response = self.client.get(
            "/model-options",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        models = body["groups"][0]["models"]

        self.assertEqual(
            [model["modelId"] for model in models], ["gpt-5.4-mini", "gpt-5.4"]
        )
        self.assertEqual(body["defaultSelection"]["modelId"], "gpt-5.4-mini")

    def test_listing_connections_reconciles_missing_builtin_preferred_model(
        self,
    ) -> None:
        created = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "preferredModelId": "gpt-legacy-does-not-exist",
                "modelPolicy": "all_catalog",
            },
        )
        self.assertEqual(created.status_code, 200)

        listing = self.client.get(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(listing.status_code, 200)
        connection = listing.json()["connections"][0]
        self.assertEqual(connection["preferredModelId"], "gpt-5.2")

    def test_model_options_filters_out_disabled_or_unconfigured_connections(
        self,
    ) -> None:
        self._set_validation_model_factory(lambda config: FakeValidationSuccessModel())
        configured = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "anthropic",
                "displayName": "Anthropic",
                "apiKey": "sk-live",
            },
        )
        self.assertEqual(configured.status_code, 200)
        self._validate_provider(configured.json()["id"])

        disabled = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "isEnabled": False,
            },
        )
        self.assertEqual(disabled.status_code, 200)

        missing_key = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "custom",
                "customSlug": "my-company",
                "displayName": "My Company Gateway",
                "baseUrl": "https://llm.example.com/v1",
                "customModels": [{"modelId": "my-model", "displayName": "My Model"}],
            },
        )
        self.assertEqual(missing_key.status_code, 200)

        response = self.client.get(
            "/model-options",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["groups"]), 1)
        self.assertEqual(body["groups"][0]["providerId"], "anthropic")

    def test_model_options_uses_custom_models_for_custom_connections(self) -> None:
        self._set_validation_model_factory(lambda config: FakeValidationSuccessModel())
        create_response = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "custom",
                "customSlug": "my-company",
                "displayName": "My Company Gateway",
                "baseUrl": "https://llm.example.com/v1",
                "apiKey": "sk-test",
                "preferredModelId": "my-model",
                "customModels": [
                    {
                        "modelId": "my-model",
                        "displayName": "My Model",
                        "enabled": True,
                    },
                    {
                        "modelId": "my-model-disabled",
                        "displayName": "Disabled Model",
                        "enabled": False,
                    },
                ],
            },
        )
        self.assertEqual(create_response.status_code, 200)
        connection_id = create_response.json()["id"]
        self._validate_provider(connection_id)

        response = self.client.get(
            "/model-options",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["groups"]), 1)
        group = body["groups"][0]
        self.assertEqual(group["connectionId"], connection_id)
        self.assertEqual(group["providerId"], "my-company")
        self.assertEqual(
            group["models"],
            [
                {
                    "connectionId": connection_id,
                    "providerLabel": "My Company Gateway",
                    "providerId": "my-company",
                    "modelId": "my-model",
                    "label": "My Model",
                    "supportsReasoningTokens": False,
                }
            ],
        )

    def test_model_options_excludes_not_validated_connections(self) -> None:
        create_response = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "preferredModelId": "gpt-5.4",
            },
        )
        self.assertEqual(create_response.status_code, 200)

        response = self.client.get(
            "/model-options",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"groups": [], "defaultSelection": None})

    def test_workspace_session_and_messages_flow(self) -> None:
        workspace_dir = self.temp_dir / "workspace-a"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        workspace_response = self.client.post(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": "nutstore-bot",
                "realPath": str(workspace_dir),
                "pathLabel": str(workspace_dir),
            },
        )
        self.assertEqual(workspace_response.status_code, 200)
        workspace_id = workspace_response.json()["id"]

        list_workspaces = self.client.get(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(list_workspaces.status_code, 200)
        self.assertEqual(len(list_workspaces.json()["workspaces"]), 1)

        provider_response = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "preferredModelId": "gpt-5.4",
            },
        )
        self.assertEqual(provider_response.status_code, 200)
        connection_id = provider_response.json()["id"]

        session_response = self.client.post(
            f"/workspaces/{workspace_id}/sessions",
            headers={"Authorization": "Bearer test-token"},
            json={
                "connectionId": connection_id,
                "modelId": "gpt-5.4",
            },
        )
        self.assertEqual(session_response.status_code, 200)
        session_id = session_response.json()["id"]
        self.assertEqual(session_response.json()["title"], "New session")
        self.assertEqual(session_response.json()["titleSource"], "placeholder")

        database = self.app.state.database
        database.execute(
            "INSERT INTO messages (id, session_id, run_id, role, content, step_id, sequence_no, created_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "msg_001",
                session_id,
                None,
                "user",
                "Refactor provider persistence",
                None,
                1,
                "2026-03-24T12:00:00Z",
                None,
            ),
        )
        database.execute(
            "UPDATE sessions SET message_count = 1, last_message_preview = ?, last_message_at = ?, updated_at = ? WHERE id = ?",
            (
                "Refactor provider persistence",
                "2026-03-24T12:00:00Z",
                "2026-03-24T12:00:00Z",
                session_id,
            ),
        )
        database.commit()

        sessions_response = self.client.get(
            f"/workspaces/{workspace_id}/sessions",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(sessions_response.status_code, 200)
        self.assertEqual(len(sessions_response.json()["sessions"]), 1)
        self.assertEqual(
            sessions_response.json()["sessions"][0]["lastMessagePreview"],
            "Refactor provider persistence",
        )

        rename_response = self.client.patch(
            f"/sessions/{session_id}",
            headers={"Authorization": "Bearer test-token"},
            json={"title": "Provider config persistence"},
        )
        self.assertEqual(rename_response.status_code, 200)
        self.assertEqual(rename_response.json()["titleSource"], "manual")

        with self.assertLogs("session_service", level=logging.INFO) as captured_logs:
            rename_response = self.client.patch(
                f"/sessions/{session_id}",
                headers={"Authorization": "Bearer test-token"},
                json={"title": "Provider config persistence v2"},
            )
        self.assertEqual(rename_response.status_code, 200)
        self.assertIn("Session renamed:", captured_logs.output[0])

        messages_response = self.client.get(
            f"/sessions/{session_id}/messages",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(messages_response.status_code, 200)
        self.assertEqual(len(messages_response.json()["messages"]), 1)
        self.assertEqual(messages_response.json()["messages"][0]["role"], "user")

    def test_session_messages_supports_pagination(self) -> None:
        workspace = self._create_workspace("workspace-message-pagination")
        provider = self._create_provider()
        session = self._create_session(
            workspace_id=str(workspace["id"]),
            connection_id=str(provider["id"]),
        )

        for content in ["message-1", "message-2", "message-3"]:
            response = self.client.post(
                f"/sessions/{session['id']}/messages",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "role": "user",
                    "content": content,
                },
            )
            self.assertEqual(response.status_code, 200)

        latest_response = self.client.get(
            f"/sessions/{session['id']}/messages?limit=2",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(latest_response.status_code, 200)
        latest_body = latest_response.json()
        self.assertEqual(
            [item["content"] for item in latest_body["messages"]],
            ["message-2", "message-3"],
        )
        self.assertEqual(latest_body["pagination"]["hasMore"], True)
        self.assertEqual(latest_body["pagination"]["nextBeforeSequence"], 2)

        older_response = self.client.get(
            f"/sessions/{session['id']}/messages?limit=2&beforeSequence=2",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(older_response.status_code, 200)
        older_body = older_response.json()
        self.assertEqual(
            [item["content"] for item in older_body["messages"]],
            ["message-1"],
        )
        self.assertEqual(older_body["pagination"]["hasMore"], False)
        self.assertIsNone(older_body["pagination"]["nextBeforeSequence"])

    def test_session_attachments_can_upload_list_and_delete(self) -> None:
        workspace = self._create_workspace("workspace-attachments")
        provider = self._create_provider()
        session = self._create_session(
            workspace_id=str(workspace["id"]),
            connection_id=str(provider["id"]),
        )

        upload_response = self.client.post(
            f"/sessions/{session['id']}/attachments",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("notes.txt", b"attachment-content", "text/plain")},
        )
        self.assertEqual(upload_response.status_code, 200)
        attachment = upload_response.json()
        self.assertEqual(attachment["fileName"], "notes.txt")
        self.assertEqual(attachment["mimeType"], "text/plain")
        self.assertEqual(attachment["sizeBytes"], len(b"attachment-content"))

        list_response = self.client.get(
            f"/sessions/{session['id']}/attachments",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.json()["attachments"]), 1)

        delete_response = self.client.delete(
            f"/sessions/{session['id']}/attachments/{attachment['id']}",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(delete_response.status_code, 204)

        list_after_delete = self.client.get(
            f"/sessions/{session['id']}/attachments",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(list_after_delete.status_code, 200)
        self.assertEqual(list_after_delete.json()["attachments"], [])

    def test_run_consumes_uploaded_attachments_and_persists_message_metadata(
        self,
    ) -> None:
        workspace = self._create_workspace("workspace-run-attachments")
        provider = self._create_provider()
        session = self._create_session(
            workspace_id=str(workspace["id"]),
            connection_id=str(provider["id"]),
        )
        self._set_sync_run_launcher()

        self.app.state.run_service = replace(
            self.app.state.run_service,
            runtime_executor=lambda *_args, **_kwargs: {
                "deltas": [],
                "steps": [],
                "final_answer": "Attachment accepted",
            },
        )

        uploaded = self.client.post(
            f"/sessions/{session['id']}/attachments",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("context.md", b"hello", "text/markdown")},
        )
        self.assertEqual(uploaded.status_code, 200)
        attachment_id = uploaded.json()["id"]

        run_response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
                "attachmentIds": [attachment_id],
                "input": "Use the attached context",
            },
        )
        self.assertEqual(run_response.status_code, 200)

        attachments_response = self.client.get(
            f"/sessions/{session['id']}/attachments",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(attachments_response.status_code, 200)
        self.assertEqual(attachments_response.json()["attachments"], [])

        messages_response = self.client.get(
            f"/sessions/{session['id']}/messages",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(messages_response.status_code, 200)
        self.assertEqual(
            messages_response.json()["messages"][0]["metadataJson"],
            '{"attachmentIds": ["' + attachment_id + '"]}',
        )

    def test_workspace_requires_existing_directory(self) -> None:
        response = self.client.post(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": "missing",
                "realPath": str(self.temp_dir / "does-not-exist"),
                "pathLabel": "missing",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_workspace_paths_must_be_unique(self) -> None:
        workspace_dir = self.temp_dir / "workspace-b"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        first = self.client.post(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": "first",
                "realPath": str(workspace_dir),
                "pathLabel": str(workspace_dir),
            },
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": "second",
                "realPath": str(workspace_dir),
                "pathLabel": str(workspace_dir),
            },
        )
        self.assertEqual(second.status_code, 400)

    def test_delete_workspace_removes_registered_workspace(self) -> None:
        workspace_dir = self.temp_dir / "workspace-delete"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        workspace = self.client.post(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": "delete-me",
                "realPath": str(workspace_dir),
                "pathLabel": str(workspace_dir),
            },
        ).json()

        delete_response = self.client.delete(
            f"/workspaces/{workspace['id']}",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(delete_response.status_code, 204)

        list_response = self.client.get(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(list_response.json()["workspaces"], [])

    def test_patch_workspace_updates_name_and_path_label(self) -> None:
        workspace_dir = self.temp_dir / "workspace-update"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        workspace = self.client.post(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": "before",
                "realPath": str(workspace_dir),
                "pathLabel": str(workspace_dir),
            },
        ).json()

        update_response = self.client.patch(
            f"/workspaces/{workspace['id']}",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": "after",
                "pathLabel": "/custom/label",
            },
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(update_response.json()["name"], "after")
        self.assertEqual(update_response.json()["pathLabel"], "/custom/label")

    def test_posting_first_user_message_sets_heuristic_title(self) -> None:
        workspace_dir = self.temp_dir / "workspace-c"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        workspace = self.client.post(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": "workspace-c",
                "realPath": str(workspace_dir),
                "pathLabel": str(workspace_dir),
            },
        ).json()

        provider = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
            },
        ).json()

        session = self.client.post(
            f"/workspaces/{workspace['id']}/sessions",
            headers={"Authorization": "Bearer test-token"},
            json={
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
            },
        ).json()

        message = self.client.post(
            f"/sessions/{session['id']}/messages",
            headers={"Authorization": "Bearer test-token"},
            json={
                "role": "user",
                "content": "Refactor provider persistence and session storage carefully",
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
            },
        )
        self.assertEqual(message.status_code, 200)

        sessions_response = self.client.get(
            f"/workspaces/{workspace['id']}/sessions",
            headers={"Authorization": "Bearer test-token"},
        )
        listed = sessions_response.json()["sessions"][0]
        self.assertEqual(listed["titleSource"], "heuristic")
        self.assertEqual(listed["messageCount"], 1)
        self.assertEqual(
            listed["title"],
            "Refactor provider persistence and session storage carefully",
        )

    def test_session_title_transitions_from_placeholder_to_heuristic_to_model(
        self,
    ) -> None:
        workspace_dir = self.temp_dir / "workspace-title-transition"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        workspace = self.client.post(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": "workspace-title-transition",
                "realPath": str(workspace_dir),
                "pathLabel": str(workspace_dir),
            },
        ).json()

        provider = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
            },
        ).json()

        session = self.client.post(
            f"/workspaces/{workspace['id']}/sessions",
            headers={"Authorization": "Bearer test-token"},
            json={
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
            },
        ).json()

        self.assertEqual(session["title"], "New session")
        self.assertEqual(session["titleSource"], "placeholder")

        user_message = self.client.post(
            f"/sessions/{session['id']}/messages",
            headers={"Authorization": "Bearer test-token"},
            json={
                "role": "user",
                "content": "Please refactor provider persistence for local session storage",
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
            },
        )
        self.assertEqual(user_message.status_code, 200)

        after_user = self.client.get(
            f"/workspaces/{workspace['id']}/sessions",
            headers={"Authorization": "Bearer test-token"},
        ).json()["sessions"][0]
        self.assertEqual(after_user["titleSource"], "heuristic")
        self.assertEqual(
            after_user["title"],
            "Please refactor provider persistence for local session st...",
        )

        assistant_message = self.client.post(
            f"/sessions/{session['id']}/messages",
            headers={"Authorization": "Bearer test-token"},
            json={
                "role": "assistant",
                "content": "Split provider configuration and local session storage into shared services.",
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
            },
        )
        self.assertEqual(assistant_message.status_code, 200)

        after_assistant = self.client.get(
            f"/workspaces/{workspace['id']}/sessions",
            headers={"Authorization": "Bearer test-token"},
        ).json()["sessions"][0]
        self.assertEqual(after_assistant["titleSource"], "model")
        self.assertEqual(
            after_assistant["title"],
            "Refactor provider persistence for local session storage",
        )
        self.assertEqual(after_assistant["messageCount"], 2)

    def test_post_run_falls_back_to_first_user_message_title_when_model_title_generation_fails(
        self,
    ) -> None:
        workspace = self._create_workspace("workspace-title-fallback")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))
        self._set_sync_run_launcher()

        def failing_title_generator(user_text: str, assistant_text: str) -> str:
            del user_text, assistant_text
            raise RuntimeError("Title generation failed")

        def fake_runtime_executor(config, run_id, user_input, auth_context, metadata):
            del config, run_id, user_input, auth_context, metadata
            return {
                "deltas": [],
                "steps": [],
                "final_answer": "Fallback title flow complete.",
            }

        self.app.state.run_service = replace(
            self.app.state.run_service,
            session_service=replace(
                self.app.state.run_service.session_service,
                model_title_generator=failing_title_generator,
            ),
            runtime_executor=fake_runtime_executor,
        )

        response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
                "input": "Please help me integrate frontend and sidecar local service with robust auth and persistence",
            },
        )
        self.assertEqual(response.status_code, 200)

        listed = self.client.get(
            f"/workspaces/{workspace['id']}/sessions",
            headers={"Authorization": "Bearer test-token"},
        ).json()["sessions"][0]
        self.assertEqual(listed["titleSource"], "heuristic")
        self.assertEqual(
            listed["title"],
            "Please help me integrate frontend and sidecar loca",
        )

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

        with self.assertLogs("provider_service", level=logging.INFO) as captured_logs:
            updated = self.client.patch(
                f"/providers/{provider_id}",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "displayName": "OpenAI Secondary",
                    "preferredModelId": "gpt-5.4",
                },
            )
        self.assertEqual(updated.status_code, 200)
        self.assertIn("Provider updated:", captured_logs.output[0])

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

    def test_update_provider_preserves_health_status_fields(self) -> None:
        self._set_validation_model_factory(lambda config: FakeValidationSuccessModel())
        created = self.client.post(
            "/providers",
            headers={"Authorization": "Bearer test-token"},
            json={
                "kind": "builtin",
                "catalogProviderId": "openai",
                "displayName": "OpenAI",
                "apiKey": "sk-test",
                "preferredModelId": "gpt-5.4",
            },
        )
        provider_id = created.json()["id"]

        validated = self.client.post(
            f"/providers/{provider_id}/validate",
            headers={"Authorization": "Bearer test-token"},
            json={},
        )
        self.assertEqual(validated.status_code, 200)
        self.assertEqual(validated.json()["healthStatus"], "connected")

        updated = self.client.patch(
            f"/providers/{provider_id}",
            headers={"Authorization": "Bearer test-token"},
            json={"preferredModelId": "gpt-5.4-mini"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["healthStatus"], "connected")
        self.assertEqual(updated.json()["healthMessage"], "Validation succeeded")
        self.assertIsNotNone(updated.json()["lastValidatedAt"])

        options = self.client.get(
            "/model-options",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(options.status_code, 200)
        groups = options.json()["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["connectionId"], provider_id)

    def test_post_run_uses_session_id_as_runtime_session_key_and_persists_output(
        self,
    ) -> None:
        workspace = self._create_workspace("workspace-run")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))
        self._set_sync_run_launcher()

        captured: dict[str, object] = {}

        def fake_runtime_executor(config, run_id, user_input, auth_context, metadata):
            captured["config"] = config
            captured["run_id"] = run_id
            captured["user_input"] = user_input
            captured["auth_context"] = auth_context
            captured["metadata"] = metadata
            return {
                "deltas": [],
                "steps": [],
                "final_answer": "Implemented sidecar run orchestration.",
            }

        self.app.state.run_service = replace(
            self.app.state.run_service,
            runtime_executor=fake_runtime_executor,
        )

        response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
                "input": "Please wire the sidecar run flow",
            },
        )
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body["run"]["status"], "completed")
        self.assertEqual(
            body["run"]["finalAnswer"], "Implemented sidecar run orchestration."
        )
        self.assertEqual(body["session"]["id"], session["id"])
        self.assertEqual(body["session"]["titleSource"], "model")
        self.assertEqual(
            body["messages"][-1]["content"], "Implemented sidecar run orchestration."
        )

        metadata = cast(RunMetadata, captured["metadata"])
        self.assertEqual(metadata.session_key, session["id"])
        self.assertEqual(metadata.workspace_path, workspace["realPath"])

        config = cast(RuntimeWorkerConfig, captured["config"])
        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.model, "gpt-5.4")
        self.assertEqual(config.api_key, "sk-test")
        self.assertEqual(config.direct_reasoning_effort, "medium")

        messages_response = self.client.get(
            f"/sessions/{session['id']}/messages",
            headers={"Authorization": "Bearer test-token"},
        )
        messages = messages_response.json()["messages"]
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])

        listed_session = self.client.get(
            f"/workspaces/{workspace['id']}/sessions",
            headers={"Authorization": "Bearer test-token"},
        ).json()["sessions"][0]
        self.assertEqual(listed_session["titleSource"], "model")

    def test_post_run_rejects_unknown_model_for_connection(self) -> None:
        workspace = self._create_workspace("workspace-run-invalid")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))
        self._set_sync_run_launcher()

        response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "not-a-real-model",
                "input": "Run this",
            },
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["run"]["status"], "failed")
        self.assertEqual(
            body["messages"][-1]["content"],
            "Run failed: Model is not available for this provider connection",
        )

        messages = self.client.get(
            f"/sessions/{session['id']}/messages",
            headers={"Authorization": "Bearer test-token"},
        ).json()["messages"]
        self.assertEqual([item["role"] for item in messages], ["user", "system"])
        self.assertEqual(
            messages[-1]["content"],
            "Run failed: Model is not available for this provider connection",
        )

    def test_post_run_accepts_explicit_reasoning_effort(self) -> None:
        workspace = self._create_workspace("workspace-run-reasoning")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))
        self._set_sync_run_launcher()

        captured: dict[str, object] = {}

        def fake_runtime_executor(config, run_id, user_input, auth_context, metadata):
            captured["config"] = config
            return {
                "deltas": [],
                "steps": [],
                "final_answer": "Reasoning set explicitly.",
            }

        self.app.state.run_service = replace(
            self.app.state.run_service,
            runtime_executor=fake_runtime_executor,
        )

        response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
                "reasoningEffort": "xhigh",
                "input": "Please think harder",
            },
        )
        self.assertEqual(response.status_code, 200)
        config = cast(RuntimeWorkerConfig, captured["config"])
        self.assertEqual(config.direct_reasoning_effort, "xhigh")

    def test_post_run_rejects_invalid_reasoning_effort_for_model(self) -> None:
        workspace = self._create_workspace("workspace-run-invalid-reasoning")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))
        self._set_sync_run_launcher()

        response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "gpt-5.4-mini",
                "reasoningEffort": "xhigh",
                "input": "Use unsupported effort",
            },
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["run"]["status"], "failed")
        self.assertEqual(body["run"]["errorCode"], "invalid_reasoning_effort")
        self.assertEqual(
            body["messages"][-1]["content"],
            "Run failed: Reasoning effort is not supported for this model",
        )

    def test_post_run_persists_system_message_for_runtime_failure(self) -> None:
        workspace = self._create_workspace("workspace-run-runtime-failure")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))
        self._set_sync_run_launcher()

        def fake_runtime_executor(config, run_id, user_input, auth_context, metadata):
            raise RuntimeProcessError("provider_error", "Upstream model request failed")

        self.app.state.run_service = replace(
            self.app.state.run_service,
            runtime_executor=fake_runtime_executor,
        )

        response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
                "input": "Trigger upstream failure",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run"]["status"], "failed")
        self.assertEqual(
            body["messages"][-1]["content"],
            "Run failed: Upstream model request failed",
        )

        with self.assertLogs("run_service", level=logging.WARNING) as captured_logs:
            response = self.client.post(
                "/runs",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "sessionId": session["id"],
                    "workspaceId": workspace["id"],
                    "connectionId": provider["id"],
                    "modelId": "gpt-5.4",
                    "input": "Trigger upstream failure again",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Run failed:", captured_logs.output[0])

        messages = self.client.get(
            f"/sessions/{session['id']}/messages",
            headers={"Authorization": "Bearer test-token"},
        ).json()["messages"]
        self.assertEqual(
            [item["role"] for item in messages],
            ["user", "system", "user", "system"],
        )
        self.assertEqual(
            messages[-1]["content"],
            "Run failed: Upstream model request failed",
        )

    def test_post_run_maps_custom_provider_runtime_config(self) -> None:
        workspace = self._create_workspace("workspace-run-custom")
        provider = self._create_custom_provider()
        self._set_sync_run_launcher()
        session = self.client.post(
            f"/workspaces/{workspace['id']}/sessions",
            headers={"Authorization": "Bearer test-token"},
            json={
                "connectionId": provider["id"],
                "modelId": "team-model",
            },
        ).json()

        captured: dict[str, object] = {}

        def fake_runtime_executor(config, run_id, user_input, auth_context, metadata):
            captured["config"] = config
            captured["run_id"] = run_id
            captured["user_input"] = user_input
            captured["auth_context"] = auth_context
            captured["metadata"] = metadata
            return {
                "deltas": [],
                "steps": [],
                "final_answer": "Custom gateway run complete.",
            }

        self.app.state.run_service = replace(
            self.app.state.run_service,
            runtime_executor=fake_runtime_executor,
        )

        response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "team-model",
                "input": "Call the custom gateway",
            },
        )
        self.assertEqual(response.status_code, 200)

        config = cast(RuntimeWorkerConfig, captured["config"])
        self.assertEqual(config.provider, "custom")
        self.assertEqual(config.base_url, "https://llm.example.com/v1")
        self.assertEqual(config.api_key, "sk-custom-test")
        self.assertEqual(config.model, "team-model")
        self.assertEqual(config.workspace_path_default, workspace["realPath"])
        self.assertEqual(config.ns_bot_home, str(nsbot_home(str(self.temp_dir))))

        metadata = cast(RunMetadata, captured["metadata"])
        self.assertEqual(metadata.session_key, session["id"])
        self.assertEqual(metadata.workspace_path, workspace["realPath"])

        body = response.json()
        self.assertEqual(body["run"]["status"], "completed")
        self.assertEqual(body["run"]["modelId"], "team-model")
        self.assertEqual(
            body["messages"][-1]["content"], "Custom gateway run complete."
        )

    def test_post_run_returns_queued_state_before_background_launcher_executes(
        self,
    ) -> None:
        workspace = self._create_workspace("workspace-run-queued")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))

        launched_tasks = []

        def fake_runtime_executor(config, run_id, user_input, auth_context, metadata):
            return {
                "deltas": [],
                "steps": [],
                "final_answer": "Queued run completed later.",
            }

        self.app.state.run_service = replace(
            self.app.state.run_service,
            runtime_executor=fake_runtime_executor,
            run_launcher=lambda task: launched_tasks.append(task),
        )

        response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
                "input": "Queue the run first",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["run"]["status"], "queued")
        self.assertEqual(body["session"]["titleSource"], "heuristic")
        self.assertEqual([item["role"] for item in body["messages"]], ["user"])
        self.assertEqual(len(launched_tasks), 1)

        launched_tasks[0]()

        events_response = self.client.get(
            f"/runs/{body['run']['id']}/events",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(events_response.status_code, 200)
        self.assertIn("event: run.completed", events_response.text)

    def test_get_run_events_replays_completed_run_sequence(self) -> None:
        workspace = self._create_workspace("workspace-run-events")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))
        self._set_sync_run_launcher()

        def fake_runtime_executor(config, run_id, user_input, auth_context, metadata):
            return {
                "deltas": [
                    {
                        "step_id": "step-1",
                        "text": "Searching workspace",
                    }
                ],
                "steps": [
                    {
                        "step_id": "step-1",
                        "step_kind": "action",
                        "model_output": "Used grep",
                        "observations": ["Found 3 files"],
                        "error": None,
                        "usage": {
                            "input_tokens": 11,
                            "output_tokens": 7,
                            "reasoning_tokens": 0,
                        },
                        "duration_ms": 120,
                        "has_delta": True,
                    }
                ],
                "final_answer": "Completed with SSE events.",
            }

        self.app.state.run_service = replace(
            self.app.state.run_service,
            runtime_executor=fake_runtime_executor,
        )

        run_response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
                "input": "Emit SSE events",
            },
        )
        self.assertEqual(run_response.status_code, 200)
        run_id = run_response.json()["run"]["id"]

        events_response = self.client.get(
            f"/runs/{run_id}/events",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(events_response.status_code, 200)
        self.assertEqual(
            events_response.headers["content-type"], "text/event-stream; charset=utf-8"
        )

        payload = events_response.text
        self.assertIn("event: run.status", payload)
        self.assertIn('"status": "queued"', payload)
        self.assertIn('"status": "running"', payload)
        self.assertIn("event: run.delta", payload)
        self.assertIn('"text": "Searching workspace"', payload)
        self.assertIn("event: run.step", payload)
        self.assertIn('"stepKind": "action"', payload)
        self.assertIn("event: run.completed", payload)
        self.assertIn('"finalAnswer": "Completed with SSE events."', payload)
        self.assertIn("event: run.replay-ready", payload)

    def test_get_run_events_replays_failed_run_sequence(self) -> None:
        workspace = self._create_workspace("workspace-run-events-failed")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))
        self._set_sync_run_launcher()

        response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "not-a-real-model",
                "input": "Fail the run",
            },
        )
        self.assertEqual(response.status_code, 400)
        run_id = response.json()["run"]["id"]

        events_response = self.client.get(
            f"/runs/{run_id}/events",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(events_response.status_code, 200)

        payload = events_response.text
        self.assertIn("event: run.failed", payload)
        self.assertIn(
            '"errorMessage": "Model is not available for this provider connection"',
            payload,
        )
        self.assertIn("event: run.message", payload)
        self.assertIn(
            '"content": "Run failed: Model is not available for this provider connection"',
            payload,
        )
        self.assertIn("event: run.replay-ready", payload)

    def test_get_run_steps_returns_persisted_planning_and_action_steps(self) -> None:
        workspace = self._create_workspace("workspace-run-steps")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))
        self._set_sync_run_launcher()

        def fake_runtime_executor(config, run_id, user_input, auth_context, metadata):
            return {
                "deltas": [],
                "steps": [
                    {
                        "step_id": "step-1",
                        "step_number": None,
                        "step_kind": "planning",
                        "plan": "Inspect the workspace and outline the approach.",
                        "model_output": "Inspect the workspace and outline the approach.",
                        "code_action": None,
                        "action_output": None,
                        "observations": [],
                        "error": None,
                        "usage": {
                            "input_tokens": 11,
                            "output_tokens": 4,
                            "reasoning_tokens": 0,
                        },
                        "duration_ms": 120,
                        "has_delta": True,
                    },
                    {
                        "step_id": "step-2",
                        "step_number": 1,
                        "step_kind": "action",
                        "plan": None,
                        "model_output": "Ignored model output",
                        "code_action": 'print("done")',
                        "action_output": {"result": "done"},
                        "observations": ["Execution logs:", "done"],
                        "error": None,
                        "usage": {
                            "input_tokens": 9,
                            "output_tokens": 3,
                            "reasoning_tokens": 0,
                        },
                        "duration_ms": 180,
                        "has_delta": False,
                    },
                ],
                "final_answer": "Done.",
            }

        self.app.state.run_service = replace(
            self.app.state.run_service,
            runtime_executor=fake_runtime_executor,
        )

        run_response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
                "input": "Persist the run steps",
            },
        )
        self.assertEqual(run_response.status_code, 200)
        run_id = run_response.json()["run"]["id"]

        steps_response = self.client.get(
            f"/runs/{run_id}/steps",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(steps_response.status_code, 200)

        body = steps_response.json()
        self.assertEqual(len(body["steps"]), 2)
        self.assertEqual(body["steps"][0]["stepKind"], "planning")
        self.assertEqual(
            body["steps"][0]["plan"], "Inspect the workspace and outline the approach."
        )
        self.assertEqual(body["steps"][1]["stepKind"], "action")
        self.assertEqual(body["steps"][1]["stepNumber"], 1)
        self.assertEqual(body["steps"][1]["codeAction"], 'print("done")')
        self.assertEqual(body["steps"][1]["actionOutput"], {"result": "done"})

    def test_cancel_run_marks_run_cancelled_and_emits_terminal_events(self) -> None:
        workspace = self._create_workspace("workspace-run-cancel")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))

        launched_tasks = []

        def fake_runtime_executor(
            config,
            run_id,
            user_input,
            auth_context,
            metadata,
            event_callback=None,
            is_cancelled=None,
        ):
            if is_cancelled is not None and is_cancelled():
                raise RuntimeProcessError("cancelled", "Run cancelled")
            return {
                "deltas": [],
                "steps": [],
                "final_answer": "Should not complete",
            }

        self.app.state.run_service = replace(
            self.app.state.run_service,
            runtime_executor=fake_runtime_executor,
            run_launcher=lambda task: launched_tasks.append(task),
        )

        run_response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "sessionId": session["id"],
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
                "input": "Cancel this run",
            },
        )
        self.assertEqual(run_response.status_code, 200)
        run_id = run_response.json()["run"]["id"]

        cancel_response = self.client.post(
            f"/runs/{run_id}/cancel",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(cancel_response.status_code, 200)
        self.assertTrue(cancel_response.json()["cancelled"])
        self.assertEqual(cancel_response.json()["run"]["status"], "cancelled")

        launched_tasks[0]()

        messages = self.client.get(
            f"/sessions/{session['id']}/messages",
            headers={"Authorization": "Bearer test-token"},
        ).json()["messages"]
        self.assertEqual(messages[-1]["content"], "Run cancelled")

        events_response = self.client.get(
            f"/runs/{run_id}/events",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(events_response.status_code, 200)
        payload = events_response.text
        self.assertIn('"status": "cancelled"', payload)
        self.assertIn('"content": "Run cancelled"', payload)
        self.assertIn("event: run.replay-ready", payload)

    def test_workspace_draft_attachments_crud(self) -> None:
        workspace = self._create_workspace("workspace-draft-attachments")

        upload_response = self.client.post(
            f"/workspaces/{workspace['id']}/draft-attachments",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("notes.txt", b"draft attachment", "text/plain")},
        )
        self.assertEqual(upload_response.status_code, 200)
        draft_attachment = upload_response.json()
        self.assertEqual(draft_attachment["workspaceId"], workspace["id"])
        self.assertEqual(draft_attachment["fileName"], "notes.txt")

        list_response = self.client.get(
            f"/workspaces/{workspace['id']}/draft-attachments",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(list_response.status_code, 200)
        items = list_response.json()["draftAttachments"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], draft_attachment["id"])

        delete_response = self.client.delete(
            f"/workspaces/{workspace['id']}/draft-attachments/{draft_attachment['id']}",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(delete_response.status_code, 204)

        list_after_delete = self.client.get(
            f"/workspaces/{workspace['id']}/draft-attachments",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(list_after_delete.status_code, 200)
        self.assertEqual(list_after_delete.json()["draftAttachments"], [])

    def test_post_run_without_session_id_promotes_draft_attachments(self) -> None:
        workspace = self._create_workspace("workspace-run-draft-attachments")
        provider = self._create_provider()
        self._set_sync_run_launcher()

        def fake_runtime_executor(*args, **kwargs):
            return {
                "deltas": [],
                "steps": [],
                "final_answer": "Draft attachment run complete.",
            }

        self.app.state.run_service = replace(
            self.app.state.run_service,
            runtime_executor=fake_runtime_executor,
        )

        upload_response = self.client.post(
            f"/workspaces/{workspace['id']}/draft-attachments",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("draft.txt", b"hello draft", "text/plain")},
        )
        self.assertEqual(upload_response.status_code, 200)
        draft_attachment_id = upload_response.json()["id"]

        run_response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "workspaceId": workspace["id"],
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
                "input": "Please process my draft attachment",
                "draftAttachmentIds": [draft_attachment_id],
            },
        )
        self.assertEqual(run_response.status_code, 200)
        body = run_response.json()
        session_id = body["session"]["id"]
        self.assertTrue(session_id.startswith("sess_"))

        records = self.app.state.repositories.attachments.list_by_session_id(session_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "consumed")
        self.assertEqual(records[0].workspace_id, workspace["id"])
        self.assertIn("draft.txt", records[0].storage_path)

        remaining_drafts = self.app.state.repositories.draft_attachments.list_by_workspace_id(
            workspace["id"]
        )
        self.assertEqual(remaining_drafts, [])

    def test_post_run_without_session_id_rejects_cross_workspace_draft_attachment(self) -> None:
        workspace_one = self._create_workspace("workspace-run-draft-a")
        workspace_two = self._create_workspace("workspace-run-draft-b")
        provider = self._create_provider()

        upload_response = self.client.post(
            f"/workspaces/{workspace_two['id']}/draft-attachments",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("cross.txt", b"cross workspace", "text/plain")},
        )
        self.assertEqual(upload_response.status_code, 200)
        draft_attachment_id = upload_response.json()["id"]

        run_response = self.client.post(
            "/runs",
            headers={"Authorization": "Bearer test-token"},
            json={
                "workspaceId": workspace_one["id"],
                "connectionId": provider["id"],
                "modelId": "gpt-5.4",
                "input": "Try wrong draft attachment",
                "draftAttachmentIds": [draft_attachment_id],
            },
        )
        self.assertEqual(run_response.status_code, 400)
        self.assertEqual(
            run_response.json()["detail"],
            "Draft attachment does not belong to this workspace",
        )

    def test_delete_session_cascades_related_records(self) -> None:
        workspace = self._create_workspace("workspace-delete-session")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))

        message_response = self.client.post(
            f"/sessions/{session['id']}/messages",
            headers={"Authorization": "Bearer test-token"},
            json={
                "role": "user",
                "content": "Delete this session",
            },
        )
        self.assertEqual(message_response.status_code, 200)

        run = self.app.state.repositories.runs.create(
            session_id=session["id"],
            workspace_id=workspace["id"],
            connection_id=str(provider["id"]),
            model_id="gpt-5.4",
            input_text="Delete this session",
        )
        self.app.state.repositories.run_steps.create(
            run_id=run.id,
            session_id=session["id"],
            step_id="step-1",
            step_kind="planning",
            plan_text="Plan before deleting session.",
        )
        self.app.state.repositories.attachments.create(
            session_id=session["id"],
            workspace_id=workspace["id"],
            file_name="evidence.txt",
            mime_type="text/plain",
            size_bytes=12,
            storage_path=f"att_{session['id']}/evidence.txt",
            status="uploaded",
        )

        delete_response = self.client.delete(
            f"/sessions/{session['id']}",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(delete_response.status_code, 204)

        sessions_response = self.client.get(
            f"/workspaces/{workspace['id']}/sessions",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(sessions_response.status_code, 200)
        self.assertEqual(sessions_response.json()["sessions"], [])

        messages_response = self.client.get(
            f"/sessions/{session['id']}/messages",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(messages_response.status_code, 404)
        self.assertEqual(messages_response.json()["detail"], "Session not found")

        self.assertEqual(
            self.app.state.repositories.attachments.list_by_session_id(session["id"]), []
        )
        self.assertEqual(
            self.app.state.repositories.messages.list_by_session_id(session["id"]), []
        )
        with self.assertRaises(ValueError):
            self.app.state.repositories.runs.get_by_id(run.id)
        self.assertEqual(self.app.state.repositories.run_steps.list_by_run_id(run.id), [])

    def test_delete_session_returns_404_for_unknown_id(self) -> None:
        response = self.client.delete(
            "/sessions/sess_not_found",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Session not found")


if __name__ == "__main__":
    unittest.main()
