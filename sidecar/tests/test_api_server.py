from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi import FastAPI, HTTPException
import requests
from smolagents.models import ChatMessageStreamDelta, Model
from smolagents.monitoring import TokenUsage

from nsbot_sidecar.api.api_server import (
    ApiServerConfig,
    create_app,
    detect_websocket_backend,
    publish_service_discovery,
)
from nsbot_sidecar.providers.direct_model import DirectModelError


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


class FakeWorkspaceSidecarIndexer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def enqueue(
        self,
        background_tasks,
        workspace_id: str,
        workspace_real_path: str,
    ) -> None:
        self.calls.append(
            {
                "background_tasks": background_tasks,
                "workspace_id": workspace_id,
                "workspace_real_path": workspace_real_path,
            }
        )


from nsbot_sidecar.api.discovery import read_service_discovery
from nsbot_sidecar.runtime.session_manager import SessionManager
from nsbot_sidecar.runtime.workspace_sidecar_indexer import WorkspaceSidecarIndexer


class ApiServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="sidecar-api-"))
        self.config = ApiServerConfig(
            host="127.0.0.1",
            port=18765,
            auth_header_value="Bearer test-token",
            ns_bot_home=str(self.temp_dir),
        )
        self.client = TestClient(create_app(self.config))

    @property
    def app(self) -> FastAPI:
        return cast(FastAPI, self.client.app)

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

    def _append_timeline_entry(
        self,
        *,
        session_id: str,
        entry_kind: str,
        display_role: str,
        content_text: str | None,
        run_id: str | None = None,
        timeline_entry_id: str | None = None,
        sequence_no: int | None = None,
        step_id: str | None = None,
        step_number: int | None = None,
        content_json: str | None = None,
        created_at: str | None = None,
    ):
        return self.app.state.repositories.timeline_entries.append(
            session_id=session_id,
            run_id=run_id,
            timeline_entry_id=timeline_entry_id,
            sequence_no=sequence_no,
            entry_kind=entry_kind,
            display_role=display_role,
            step_id=step_id,
            step_number=step_number,
            content_text=content_text,
            content_json=content_json,
            created_at=created_at,
        )

    def _get_session_timeline(self, session_id: str) -> list[dict[str, object]]:
        response = self.client.get(
            f"/sessions/{session_id}/timeline",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["entries"]

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

    def test_health_allows_tauri_origin(self) -> None:
        response = self.client.get(
            "/health",
            headers={"Origin": "tauri://localhost"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "tauri://localhost",
        )

    def test_auth_check_endpoint_removed(self) -> None:
        response = self.client.get("/auth/check")
        self.assertEqual(response.status_code, 404)

    def test_publish_service_discovery(self) -> None:
        path = publish_service_discovery(self.config)
        self.assertTrue(path.exists())

        discovery = read_service_discovery(str(self.temp_dir))
        self.assertEqual(discovery.base_url, "http://127.0.0.1:18765")
        self.assertEqual(discovery.port, 18765)
        self.assertEqual(discovery.token, "test-token")
        self.assertGreater(discovery.pid, 0)

    def test_invalid_host_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            create_app(
                ApiServerConfig(
                    host="0.0.0.0",
                    auth_header_value="Bearer bad-token",
                )
            )

    def test_detect_websocket_backend_prefers_websockets(self) -> None:
        def fake_find_spec(name: str):
            if name == "websockets":
                return object()
            if name == "wsproto":
                return object()
            return None

        with patch("nsbot_sidecar.api.api_server.importlib.util.find_spec", side_effect=fake_find_spec):
            self.assertEqual(detect_websocket_backend(), "websockets")

    def test_detect_websocket_backend_raises_when_missing(self) -> None:
        with patch(
            "nsbot_sidecar.api.api_server.importlib.util.find_spec",
            return_value=None,
        ):
            with self.assertRaisesRegex(RuntimeError, "WebSocket backend"):
                detect_websocket_backend()

    def test_provider_catalog_returns_custom_template_without_auth(self) -> None:
        response = self.client.get("/provider-catalog")
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
            "nsbot_sidecar.application.provider_service", level=logging.WARNING
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

    def test_model_options_no_auth_required(self) -> None:
        response = self.client.get("/model-options")
        self.assertEqual(response.status_code, 200)

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
        self._append_timeline_entry(
            session_id=session_id,
            timeline_entry_id="msg_001",
            entry_kind="user_input",
            display_role="user",
            content_text="Refactor provider persistence",
            sequence_no=1,
            created_at="2026-03-24T12:00:00Z",
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

        with self.assertLogs("nsbot_sidecar.application.session_service", level=logging.INFO) as captured_logs:
            rename_response = self.client.patch(
                f"/sessions/{session_id}",
                headers={"Authorization": "Bearer test-token"},
                json={"title": "Provider config persistence v2"},
            )
        self.assertEqual(rename_response.status_code, 200)
        self.assertIn("Session renamed:", captured_logs.output[0])

        entries = self._get_session_timeline(session_id)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["displayRole"], "user")

    def test_session_timeline_supports_pagination(self) -> None:
        workspace = self._create_workspace("workspace-message-pagination")
        provider = self._create_provider()
        session = self._create_session(
            workspace_id=str(workspace["id"]),
            connection_id=str(provider["id"]),
        )

        for index, content in enumerate(
            ["message-1", "message-2", "message-3"], start=1
        ):
            self._append_timeline_entry(
                session_id=str(session["id"]),
                entry_kind="user_input",
                display_role="user",
                content_text=content,
                sequence_no=index,
                created_at=f"2026-03-24T12:00:0{index}Z",
            )

        self.app.state.session_service.timeline_service.refresh_session_summary(
            str(session["id"])
        )

        latest_response = self.client.get(
            f"/sessions/{session['id']}/timeline?limit=2",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(latest_response.status_code, 200)
        latest_body = latest_response.json()
        self.assertEqual(
            [item["contentText"] for item in latest_body["entries"]],
            ["message-2", "message-3"],
        )
        self.assertEqual(latest_body["pagination"]["hasMore"], True)
        self.assertEqual(latest_body["pagination"]["nextBeforeSequence"], 2)

        older_response = self.client.get(
            f"/sessions/{session['id']}/timeline?limit=2&beforeSequence=2",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(older_response.status_code, 200)
        older_body = older_response.json()
        self.assertEqual(
            [item["contentText"] for item in older_body["entries"]],
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

    def test_workspace_creation_enqueues_sidecar_indexing(self) -> None:
        workspace_dir = self.temp_dir / "workspace-sidecar-index"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        fake_indexer = FakeWorkspaceSidecarIndexer()
        object.__setattr__(
            self.app.state.session_service,
            "workspace_sidecar_indexer",
            fake_indexer,
        )

        response = self.client.post(
            "/workspaces",
            headers={"Authorization": "Bearer test-token"},
            json={
                "name": "workspace-sidecar-index",
                "realPath": str(workspace_dir),
                "pathLabel": str(workspace_dir),
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(len(fake_indexer.calls), 1)
        self.assertEqual(fake_indexer.calls[0]["workspace_id"], body["id"])
        self.assertEqual(
            fake_indexer.calls[0]["workspace_real_path"], str(workspace_dir.resolve())
        )
        self.assertIsNotNone(fake_indexer.calls[0]["background_tasks"])

    def test_workspace_sidecar_index_status_returns_disabled_when_indexer_not_configured(
        self,
    ) -> None:
        object.__setattr__(
            self.app.state.session_service, "workspace_sidecar_indexer", None
        )
        workspace = self._create_workspace("workspace-no-indexer")

        response = self.client.get(
            f"/workspaces/{workspace['id']}/sidecar-index/status",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["workspaceId"], workspace["id"])
        self.assertEqual(body["status"], "disabled")
        self.assertEqual(body["manifestExists"], False)
        self.assertEqual(body["stats"]["converted"], 0)

    def test_workspace_sidecar_index_status_reads_manifest_payload(self) -> None:
        workspace = self._create_workspace("workspace-index-status")
        object.__setattr__(
            self.app.state.session_service,
            "workspace_sidecar_indexer",
            WorkspaceSidecarIndexer(),
        )

        workspace_path = Path(cast(str, workspace["realPath"]))
        sidecar_root = workspace_path / ".sidecar"
        sidecar_root.mkdir(parents=True, exist_ok=True)
        manifest_path = sidecar_root / ".index-manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "lastIndexedAt": "2026-03-30T12:00:00+00:00",
                    "stats": {
                        "scanned": 8,
                        "converted": 3,
                        "skipped": 5,
                        "failed": 0,
                    },
                    "sources": {
                        "a.pdf": {
                            "signature": {"mtimeNs": 1, "size": 2},
                            "output": "a.pdf.md",
                            "updatedAt": "2026-03-30T12:00:00+00:00",
                        },
                        "nested/b.xlsx": {
                            "signature": {"mtimeNs": 2, "size": 3},
                            "output": "nested/b.xlsx.csv",
                            "updatedAt": "2026-03-30T12:00:00+00:00",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        response = self.client.get(
            f"/workspaces/{workspace['id']}/sidecar-index/status",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "indexed")
        self.assertEqual(body["lastIndexedAt"], "2026-03-30T12:00:00+00:00")
        self.assertEqual(body["sourceCount"], 2)
        self.assertEqual(
            body["stats"], {"scanned": 8, "converted": 3, "skipped": 5, "failed": 0}
        )

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

        message_text = "Refactor provider persistence and session storage carefully"
        self._append_timeline_entry(
            session_id=str(session["id"]),
            entry_kind="user_input",
            display_role="user",
            content_text=message_text,
        )
        self.app.state.session_service.apply_first_user_message_title(
            str(session["id"]),
            message_text,
            active_connection_id=str(provider["id"]),
            active_model_id="gpt-5.4",
        )
        self.app.state.session_service.timeline_service.refresh_session_summary(
            str(session["id"]),
            active_connection_id=str(provider["id"]),
            active_model_id="gpt-5.4",
        )

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

        user_text = "Please refactor provider persistence for local session storage"
        self._append_timeline_entry(
            session_id=str(session["id"]),
            entry_kind="user_input",
            display_role="user",
            content_text=user_text,
        )
        self.app.state.session_service.apply_first_user_message_title(
            str(session["id"]),
            user_text,
            active_connection_id=str(provider["id"]),
            active_model_id="gpt-5.4",
        )
        self.app.state.session_service.timeline_service.refresh_session_summary(
            str(session["id"]),
            active_connection_id=str(provider["id"]),
            active_model_id="gpt-5.4",
        )

        after_user = self.client.get(
            f"/workspaces/{workspace['id']}/sessions",
            headers={"Authorization": "Bearer test-token"},
        ).json()["sessions"][0]
        self.assertEqual(after_user["titleSource"], "heuristic")
        self.assertEqual(
            after_user["title"],
            "Please refactor provider persistence for local session st...",
        )

        self._append_timeline_entry(
            session_id=str(session["id"]),
            entry_kind="final_answer",
            display_role="assistant",
            content_text="Split provider configuration and local session storage into shared services.",
        )
        self.app.state.session_service.timeline_service.refresh_session_summary(
            str(session["id"]),
            active_connection_id=str(provider["id"]),
            active_model_id="gpt-5.4",
        )
        self.app.state.session_service.generate_model_title(str(session["id"]))

        after_assistant = self.client.get(
            f"/workspaces/{workspace['id']}/sessions",
            headers={"Authorization": "Bearer test-token"},
        ).json()["sessions"][0]
        self.assertEqual(after_assistant["titleSource"], "model")
        self.assertEqual(
            after_assistant["title"],
            "Please refactor provider persistence for local session st...",
        )
        self.assertEqual(after_assistant["messageCount"], 2)



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

        with self.assertLogs("nsbot_sidecar.application.provider_service", level=logging.INFO) as captured_logs:
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

    def test_delete_provider_returns_conflict_when_referenced(self) -> None:
        workspace = self._create_workspace("workspace-provider-delete-conflict")
        provider = self._create_provider()
        self._create_session(workspace["id"], str(provider["id"]))

        deleted = self.client.delete(
            f"/providers/{provider['id']}",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(deleted.status_code, 409)
        detail = deleted.json().get("detail", "")
        self.assertIn("still referenced", detail)

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



    def test_delete_session_cascades_related_records(self) -> None:
        workspace = self._create_workspace("workspace-delete-session")
        provider = self._create_provider()
        session = self._create_session(workspace["id"], str(provider["id"]))
        runtime_sessions = SessionManager(str(self.temp_dir))
        runtime_session = runtime_sessions.get_or_create(str(session["id"]))
        runtime_session.messages.append(
            {"role": "user", "content": "Delete this session"}
        )
        runtime_sessions.save(runtime_session)
        runtime_session_path = runtime_sessions.session_path(str(session["id"]))
        self.assertTrue(runtime_session_path.exists())

        self._append_timeline_entry(
            session_id=str(session["id"]),
            entry_kind="user_input",
            display_role="user",
            content_text="Delete this session",
        )

        run = self.app.state.repositories.runs.create(
            session_id=session["id"],
            workspace_id=workspace["id"],
            connection_id=str(provider["id"]),
            model_id="gpt-5.4",
            input_text="Delete this session",
        )
        self.app.state.repositories.timeline_entries.append(
            session_id=session["id"],
            run_id=run.id,
            entry_kind="planning",
            display_role="assistant",
            step_id="step-1",
            content_text="Plan before deleting session.",
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
        self.assertFalse(runtime_session_path.exists())

        sessions_response = self.client.get(
            f"/workspaces/{workspace['id']}/sessions",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(sessions_response.status_code, 200)
        self.assertEqual(sessions_response.json()["sessions"], [])

        messages_response = self.client.get(
            f"/sessions/{session['id']}/timeline",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(messages_response.status_code, 404)
        self.assertEqual(messages_response.json()["detail"], "Session not found")

        self.assertEqual(
            self.app.state.repositories.attachments.list_by_session_id(session["id"]),
            [],
        )
        self.assertEqual(
            self.app.state.repositories.timeline_entries.list_by_session_id(
                session["id"]
            ),
            [],
        )
        with self.assertRaises(ValueError):
            self.app.state.repositories.runs.get_by_id(run.id)
        self.assertEqual(
            self.app.state.repositories.timeline_entries.list_by_run_id(run.id), []
        )

    def test_delete_session_returns_404_for_unknown_id(self) -> None:
        response = self.client.delete(
            "/sessions/sess_not_found",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Session not found")


class ApiServerRuntimeSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="sidecar-api-runtime-"))

    def _reserve_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _start_real_server(self, port: int) -> subprocess.Popen[str]:
        env = os.environ.copy()
        env["NS_BOT_HOST"] = "127.0.0.1"
        env["NS_BOT_PORT"] = str(port)
        env["NS_BOT_HOME"] = str(self.temp_dir)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
        env["PYTHONUNBUFFERED"] = "1"
        return subprocess.Popen(
            [sys.executable, "-m", "nsbot_sidecar.api.api_server"],
            cwd=str(Path(__file__).resolve().parent.parent),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _wait_for_server_ready(
        self, process: subprocess.Popen[str], base_url: str, timeout_seconds: float = 8.0
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=1)
                self.fail(
                    "sidecar exited before becoming ready\n"
                    f"exit={process.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
                )
            try:
                response = requests.get(f"{base_url}/health", timeout=0.2)
                if response.status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.1)

        process.terminate()
        stdout, stderr = process.communicate(timeout=3)
        self.fail(
            "sidecar did not become ready in time\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        )

    def test_real_uvicorn_process_accepts_acp_websocket(self) -> None:
        port = self._reserve_free_port()
        process = self._start_real_server(port)
        base_url = f"http://127.0.0.1:{port}"

        try:
            self._wait_for_server_ready(process, base_url)

            async def run_handshake() -> dict[str, Any]:
                import websockets

                async with websockets.connect(f"ws://127.0.0.1:{port}/acp/ws") as websocket:
                    await websocket.send(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "initialize",
                                "params": {
                                    "protocolVersion": 1,
                                    "clientCapabilities": {
                                        "fs": {
                                            "readTextFile": False,
                                            "writeTextFile": False,
                                        },
                                        "terminal": False,
                                    },
                                    "clientInfo": {
                                        "name": "runtime-smoke-test",
                                        "title": "Runtime Smoke Test",
                                        "version": "0.1.0",
                                    },
                                },
                            }
                        )
                    )
                    return json.loads(await websocket.recv())

            response = asyncio.run(run_handshake())
            self.assertEqual(response["id"], 1)
            self.assertEqual(response["result"]["protocolVersion"], 1)
            self.assertEqual(response["result"]["agentInfo"]["name"], "nutstore-sidecar")
        finally:
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
