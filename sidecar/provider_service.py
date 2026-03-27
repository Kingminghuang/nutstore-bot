from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Callable

from fastapi import HTTPException, status

from direct_model import DirectModel, DirectModelConfig, DirectModelError
from provider_catalog import BUILTIN_PROVIDERS, catalog_version, list_providers
from redaction import redact_sensitive
from sensitive_write_guard import detect_sensitive_write_issues
from repositories import (
    ProviderConnectionBundle,
    ProviderConnectionsRepository,
    ProviderHeaderRecord,
    ProviderModelRecord,
    create_id,
    now_iso_timestamp,
)
from secret_store import LocalSecretStore, ProviderSecretPayload


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderService:
    repositories: ProviderConnectionsRepository
    secret_store: LocalSecretStore
    model_factory: Callable[[DirectModelConfig], Any] | None = None

    def _probe_provider_connection(
        self,
        *,
        runtime_provider: str,
        base_url: str,
        api_key: str,
        model_id: str,
    ) -> None:
        config = DirectModelConfig(
            provider=runtime_provider,
            base_url=base_url,
            api_key=api_key,
            model_id=model_id,
            timeout_seconds=10.0,
        )
        model = (
            self.model_factory(config)
            if self.model_factory is not None
            else DirectModel(config)
        )

        if hasattr(model, "generate_stream"):
            stream = model.generate_stream(
                messages=[{"role": "user", "content": "Reply with OK only."}]
            )
            for _ in stream:
                break
            return

        if hasattr(model, "generate"):
            model.generate(
                messages=[{"role": "user", "content": "Reply with OK only."}]
            )
            return

        raise RuntimeError("Model client does not support validation probes")

    def catalog_payload(self) -> dict[str, Any]:
        providers = list_providers()
        providers.append(custom_provider_template())
        return {"version": catalog_version(providers), "providers": providers}

    def list_connections_payload(self) -> dict[str, list[dict[str, Any]]]:
        bundles = self._reconcile_catalog_preferences(self.repositories.list_bundles())
        return {
            "connections": [
                serialize_bundle(bundle, self.secret_store) for bundle in bundles
            ]
        }

    def model_options_payload(self) -> dict[str, Any]:
        catalog_entries = list_providers()
        catalog_by_id = {str(entry["id"]): entry for entry in catalog_entries}
        groups: list[dict[str, Any]] = []
        preferred_by_connection: dict[str, str] = {}

        for bundle in self._reconcile_catalog_preferences(
            self.repositories.list_bundles()
        ):
            connection = bundle.connection
            if (
                not connection.is_enabled
                or not connection.api_key_configured
                or connection.health_status != "connected"
            ):
                continue

            provider_id = (
                connection.catalog_provider_id
                or connection.custom_slug
                or connection.runtime_provider
            )
            models: list[dict[str, Any]] = []

            if connection.kind == "builtin":
                catalog_provider_id = connection.catalog_provider_id or ""
                catalog_entry = catalog_by_id.get(catalog_provider_id)
                if catalog_entry is None:
                    continue

                catalog_models = list(catalog_entry.get("models", []))
                if connection.model_policy == "restricted":
                    catalog_models_by_id = {
                        str(model.get("id") or ""): model for model in catalog_models
                    }
                    enabled_model_ids = [
                        model.model_id
                        for model in bundle.models
                        if model.source == "catalog" and model.enabled
                    ]
                    catalog_models = [
                        catalog_models_by_id[model_id]
                        for model_id in enabled_model_ids
                        if model_id in catalog_models_by_id
                    ]

                models = [
                    serialize_catalog_model_option(
                        connection_id=connection.id,
                        provider_label=connection.display_name,
                        provider_id=provider_id,
                        model=model,
                    )
                    for model in catalog_models
                ]
            elif connection.kind == "custom":
                models = [
                    serialize_custom_model_option(
                        connection_id=connection.id,
                        provider_label=connection.display_name,
                        provider_id=provider_id,
                        model=model,
                    )
                    for model in bundle.models
                    if model.source == "custom" and model.enabled
                ]

            if not models:
                continue

            if connection.preferred_model_id:
                preferred_by_connection[connection.id] = connection.preferred_model_id

            groups.append(
                {
                    "connectionId": connection.id,
                    "providerLabel": connection.display_name,
                    "providerId": provider_id,
                    "models": models,
                }
            )

        return {
            "groups": groups,
            "defaultSelection": select_default_model(groups, preferred_by_connection),
        }

    def create_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        bundle = self._upsert_provider(payload, existing=None)
        return serialize_bundle(bundle, self.secret_store)

    def update_provider(
        self, provider_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        existing = self.repositories.get_bundle_by_id(provider_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found"
            )

        merged = dict(payload)
        merged["id"] = provider_id
        bundle = self._upsert_provider(merged, existing=existing)
        LOGGER.info(
            "Provider updated: provider_id=%s kind=%s runtime_provider=%s display_name=%s",
            bundle.connection.id,
            bundle.connection.kind,
            bundle.connection.runtime_provider,
            bundle.connection.display_name,
        )
        return serialize_bundle(bundle, self.secret_store)

    def delete_provider(self, provider_id: str) -> None:
        bundle = self.repositories.get_bundle_by_id(provider_id)
        if bundle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found"
            )

        self.repositories.delete_by_id(provider_id)
        self.secret_store.delete_provider_secret(bundle.connection.secret_ref)

    def validate_provider(
        self, provider_id: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        bundle = self.repositories.get_bundle_by_id(provider_id)
        if bundle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found"
            )

        model_id = self._resolve_validation_model_id(bundle, payload or {})
        secret_payload = self.secret_store.load_provider_secret(
            bundle.connection.secret_ref
        )
        api_key = secret_payload.api_key if secret_payload is not None else None
        if api_key is None or api_key.strip() == "":
            return self._persist_validation_result(
                bundle,
                {
                    "ok": False,
                    "providerId": provider_id,
                    "modelId": model_id,
                    "errorCode": "missing_api_key",
                    "errorMessage": "Provider connection is missing an API key",
                },
                health_status="invalid_config",
                health_message="Missing API key",
            )

        base_url = bundle.connection.base_url or ""
        if (
            bundle.connection.runtime_provider not in BUILTIN_PROVIDERS
            and base_url.strip() == ""
        ):
            return self._persist_validation_result(
                bundle,
                {
                    "ok": False,
                    "providerId": provider_id,
                    "modelId": model_id,
                    "errorCode": "missing_base_url",
                    "errorMessage": "Provider connection is missing a base URL",
                },
                health_status="invalid_config",
                health_message="Missing base URL",
            )

        try:
            self._probe_provider_connection(
                runtime_provider=bundle.connection.runtime_provider,
                base_url=base_url,
                api_key=api_key,
                model_id=model_id,
            )
        except DirectModelError as exc:
            return self._persist_validation_result(
                bundle,
                {
                    "ok": False,
                    "providerId": provider_id,
                    "modelId": model_id,
                    "errorCode": exc.code,
                    "errorMessage": exc.message,
                },
                health_status=_health_status_from_error_code(exc.code),
                health_message=exc.message,
            )
        except Exception as exc:  # noqa: BLE001
            return self._persist_validation_result(
                bundle,
                {
                    "ok": False,
                    "providerId": provider_id,
                    "modelId": model_id,
                    "errorCode": "validation_failed",
                    "errorMessage": str(exc),
                },
                health_status="invalid_config",
                health_message=str(exc),
            )

        return self._persist_validation_result(
            bundle,
            {
                "ok": True,
                "providerId": provider_id,
                "modelId": model_id,
                "runtimeProvider": bundle.connection.runtime_provider,
                "baseUrl": bundle.connection.base_url,
            },
            health_status="connected",
            health_message="Validation succeeded",
        )

    def _upsert_provider(
        self,
        payload: dict[str, Any],
        *,
        existing: ProviderConnectionBundle | None,
    ) -> ProviderConnectionBundle:
        normalized = normalize_provider_payload(payload, existing)
        for header in normalized["headers"]:
            if header.get("id") in {None, ""}:
                header["id"] = create_id("hdr")

        existing_secret = (
            self.secret_store.load_provider_secret(existing.connection.secret_ref)
            if existing is not None
            else None
        ) or ProviderSecretPayload(version=1, api_key=None, secret_headers={})

        secret_ref = (
            existing.connection.secret_ref
            if existing is not None
            else normalized["secret_ref"]
        )
        secret_payload = build_secret_payload(normalized, existing_secret)

        sensitive_issues = detect_sensitive_write_issues(
            {
                "connection_data": normalized,
                "models": normalized["models"],
                "headers": normalized["headers"],
            }
        )
        if sensitive_issues:
            LOGGER.warning(
                "Blocked provider persistence due to sensitive values in non-secret fields: provider_id=%s issues=%s payload=%s",
                normalized["id"],
                ", ".join(sensitive_issues),
                redact_sensitive(
                    {
                        "display_name": normalized.get("display_name"),
                        "base_url": normalized.get("base_url"),
                        "models": normalized.get("models"),
                        "headers": normalized.get("headers"),
                    }
                ),
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Sensitive data detected in non-secret persisted fields",
            )

        bundle = self.repositories.save_bundle(
            connection_data={
                "id": normalized["id"],
                "kind": normalized["kind"],
                "runtime_provider": normalized["runtime_provider"],
                "catalog_provider_id": normalized.get("catalog_provider_id"),
                "custom_slug": normalized.get("custom_slug"),
                "display_name": normalized["display_name"],
                "base_url": normalized.get("base_url"),
                "secret_ref": secret_ref,
                "api_key_configured": normalized["api_key_configured"],
                "health_status": normalized.get("health_status"),
                "health_message": normalized.get("health_message"),
                "last_validated_at": normalized.get("last_validated_at"),
                "model_policy": normalized["model_policy"],
                "preferred_model_id": normalized.get("preferred_model_id"),
                "is_enabled": normalized["is_enabled"],
            },
            models=normalized["models"],
            headers=normalized["headers"],
        )
        self.secret_store.save_provider_secret(
            bundle.connection.secret_ref, secret_payload
        )
        return bundle

    def _resolve_validation_model_id(
        self, bundle: ProviderConnectionBundle, payload: dict[str, Any]
    ) -> str:
        requested_model_id = _normalize_optional_string(
            payload.get("modelId", payload.get("model_id"))
        )
        if requested_model_id is not None:
            return requested_model_id

        preferred_model_id = _normalize_optional_string(
            bundle.connection.preferred_model_id
        )
        if preferred_model_id is not None:
            return preferred_model_id

        if bundle.connection.kind == "custom":
            for model in bundle.models:
                if model.source == "custom" and model.enabled:
                    return model.model_id
        else:
            catalog_by_id = {provider["id"]: provider for provider in list_providers()}
            catalog_provider_id = bundle.connection.catalog_provider_id or ""
            catalog_entry = catalog_by_id.get(catalog_provider_id)
            if catalog_entry is not None:
                models = catalog_entry.get("models") or []
                if models:
                    first_model_id = _normalize_optional_string(models[0].get("id"))
                    if first_model_id is not None:
                        return first_model_id

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider connection has no model available for validation",
        )

    def _persist_validation_result(
        self,
        bundle: ProviderConnectionBundle,
        payload: dict[str, Any],
        *,
        health_status: str,
        health_message: str,
    ) -> dict[str, Any]:
        persisted_bundle = self.repositories.save_bundle(
            connection_data={
                "id": bundle.connection.id,
                "kind": bundle.connection.kind,
                "runtime_provider": bundle.connection.runtime_provider,
                "catalog_provider_id": bundle.connection.catalog_provider_id,
                "custom_slug": bundle.connection.custom_slug,
                "display_name": bundle.connection.display_name,
                "base_url": bundle.connection.base_url,
                "secret_ref": bundle.connection.secret_ref,
                "api_key_configured": bundle.connection.api_key_configured,
                "health_status": health_status,
                "health_message": health_message,
                "last_validated_at": now_iso_timestamp(),
                "model_policy": bundle.connection.model_policy,
                "preferred_model_id": bundle.connection.preferred_model_id,
                "is_enabled": bundle.connection.is_enabled,
            },
            models=[
                {
                    "id": model.id,
                    "source": model.source,
                    "model_id": model.model_id,
                    "display_name": model.display_name,
                    "enabled": model.enabled,
                    "sort_order": model.sort_order,
                }
                for model in bundle.models
            ],
            headers=[
                {
                    "id": header.id,
                    "name": header.name,
                    "value_kind": header.value_kind,
                    "plain_value": header.plain_value,
                    "sort_order": header.sort_order,
                }
                for header in bundle.headers
            ],
        )
        payload["healthStatus"] = persisted_bundle.connection.health_status
        payload["healthMessage"] = persisted_bundle.connection.health_message
        payload["lastValidatedAt"] = persisted_bundle.connection.last_validated_at
        return payload

    def _reconcile_catalog_preferences(
        self, bundles: list[ProviderConnectionBundle]
    ) -> list[ProviderConnectionBundle]:
        catalog_by_id = {provider["id"]: provider for provider in list_providers()}
        reconciled: list[ProviderConnectionBundle] = []
        for bundle in bundles:
            if bundle.connection.kind != "builtin":
                reconciled.append(bundle)
                continue

            preferred_model_id = bundle.connection.preferred_model_id
            if preferred_model_id is None:
                reconciled.append(bundle)
                continue

            catalog_entry = catalog_by_id.get(
                bundle.connection.catalog_provider_id or ""
            )
            catalog_models = {
                str(model.get("id") or "")
                for model in (catalog_entry or {}).get("models", [])
            }
            if preferred_model_id in catalog_models:
                reconciled.append(bundle)
                continue

            next_preferred_model_id = None
            if bundle.connection.model_policy == "restricted":
                for model in bundle.models:
                    if (
                        model.source == "catalog"
                        and model.enabled
                        and model.model_id in catalog_models
                    ):
                        next_preferred_model_id = model.model_id
                        break
            elif catalog_entry and catalog_entry.get("models"):
                next_preferred_model_id = _normalize_optional_string(
                    catalog_entry["models"][0].get("id")
                )

            updated_bundle = self.repositories.save_bundle(
                connection_data={
                    "id": bundle.connection.id,
                    "kind": bundle.connection.kind,
                    "runtime_provider": bundle.connection.runtime_provider,
                    "catalog_provider_id": bundle.connection.catalog_provider_id,
                    "custom_slug": bundle.connection.custom_slug,
                    "display_name": bundle.connection.display_name,
                    "base_url": bundle.connection.base_url,
                    "secret_ref": bundle.connection.secret_ref,
                    "api_key_configured": bundle.connection.api_key_configured,
                    "health_status": bundle.connection.health_status,
                    "health_message": bundle.connection.health_message,
                    "last_validated_at": bundle.connection.last_validated_at,
                    "model_policy": bundle.connection.model_policy,
                    "preferred_model_id": next_preferred_model_id,
                    "is_enabled": bundle.connection.is_enabled,
                },
                models=[
                    {
                        "id": model.id,
                        "source": model.source,
                        "model_id": model.model_id,
                        "display_name": model.display_name,
                        "enabled": model.enabled,
                        "sort_order": model.sort_order,
                    }
                    for model in bundle.models
                ],
                headers=[
                    {
                        "id": header.id,
                        "name": header.name,
                        "value_kind": header.value_kind,
                        "plain_value": header.plain_value,
                        "sort_order": header.sort_order,
                    }
                    for header in bundle.headers
                ],
            )
            reconciled.append(updated_bundle)
        return reconciled


def custom_provider_template() -> dict[str, Any]:
    return {
        "id": "custom",
        "label": "Custom OpenAI-Compatible",
        "kind": "custom-template",
        "runtimeProvider": "custom",
        "baseUrlPolicy": "required",
        "models": [],
    }


def normalize_provider_payload(
    payload: dict[str, Any], existing: ProviderConnectionBundle | None
) -> dict[str, Any]:
    kind = str(
        payload.get("kind") or (existing.connection.kind if existing else "")
    ).strip()
    if kind not in {"builtin", "custom"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid provider kind"
        )

    catalog_by_id = {provider["id"]: provider for provider in list_providers()}
    provider_id = (
        str(payload.get("id") or (existing.connection.id if existing else "")).strip()
        or None
    )

    if kind == "builtin":
        catalog_provider_id = str(
            payload.get("catalogProviderId")
            or payload.get("catalog_provider_id")
            or (existing.connection.catalog_provider_id if existing else "")
        ).strip()
        if catalog_provider_id not in BUILTIN_PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown builtin provider",
            )

        catalog_entry = catalog_by_id[catalog_provider_id]
        runtime_provider = str(catalog_entry["runtimeProvider"])
        base_url_policy = str(catalog_entry["baseUrlPolicy"])
        base_url_input = payload.get("baseUrl", payload.get("base_url"))
        if base_url_input is None and existing is not None:
            base_url_input = existing.connection.base_url
        base_url = _normalize_optional_string(base_url_input)
        if base_url_policy == "hidden":
            base_url = None

        display_name = _normalize_optional_string(
            payload.get("displayName", payload.get("display_name"))
        )
        if display_name is None and existing is not None:
            display_name = existing.connection.display_name
        if display_name is None:
            display_name = str(catalog_entry["label"])

        model_policy = str(
            payload.get("modelPolicy")
            or payload.get("model_policy")
            or (existing.connection.model_policy if existing else "all_catalog")
        )
        enabled_model_ids = payload.get("enabledModelIds")
        if enabled_model_ids is None and existing is not None:
            enabled_model_ids = [
                model.model_id
                for model in existing.models
                if model.source == "catalog" and model.enabled
            ]
        enabled_model_ids = _normalize_string_list(enabled_model_ids)

        models = [
            {
                "id": (
                    existing_model.id
                    if (
                        existing_model := _find_existing_model(
                            existing, "catalog", model_id
                        )
                    )
                    else None
                ),
                "source": "catalog",
                "model_id": model_id,
                "display_name": None,
                "enabled": True,
                "sort_order": index,
            }
            for index, model_id in enumerate(enabled_model_ids)
        ]
        headers = normalize_headers(payload.get("headers"), existing)
        preferred_model_id = _normalize_optional_string(
            payload.get("preferredModelId", payload.get("preferred_model_id"))
        )
        if preferred_model_id is None and existing is not None:
            preferred_model_id = existing.connection.preferred_model_id

        return {
            "id": provider_id,
            "kind": "builtin",
            "runtime_provider": runtime_provider,
            "catalog_provider_id": catalog_provider_id,
            "custom_slug": None,
            "display_name": display_name,
            "base_url": base_url,
            "secret_ref": existing.connection.secret_ref
            if existing
            else f"sec_{provider_id or 'provider'}",
            "api_key_input": payload.get(
                "apiKey", payload.get("api_key", _API_KEY_SENTINEL)
            ),
            "api_key_configured": resolve_api_key_configured(payload, existing),
            "health_status": payload.get("healthStatus", payload.get("health_status"))
            if ("healthStatus" in payload or "health_status" in payload)
            else (existing.connection.health_status if existing else "unknown"),
            "health_message": payload.get(
                "healthMessage", payload.get("health_message")
            )
            if ("healthMessage" in payload or "health_message" in payload)
            else (existing.connection.health_message if existing else None),
            "last_validated_at": payload.get(
                "lastValidatedAt", payload.get("last_validated_at")
            )
            if ("lastValidatedAt" in payload or "last_validated_at" in payload)
            else (existing.connection.last_validated_at if existing else None),
            "model_policy": model_policy,
            "preferred_model_id": preferred_model_id,
            "is_enabled": payload.get("isEnabled", payload.get("is_enabled", True))
            is not False,
            "models": models,
            "headers": headers,
        }

    custom_slug = _normalize_optional_string(
        payload.get("customSlug", payload.get("custom_slug"))
        or (existing.connection.custom_slug if existing else None)
    )
    if custom_slug is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Custom provider slug is required",
        )

    base_url_value = payload.get("baseUrl", payload.get("base_url", _VALUE_UNSET))
    if base_url_value is _VALUE_UNSET and existing is not None:
        base_url_value = existing.connection.base_url
    base_url = (
        None
        if base_url_value is _VALUE_UNSET
        else _normalize_optional_string(base_url_value)
    )
    if base_url is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Base URL is required for custom providers",
        )

    display_name = _normalize_optional_string(
        payload.get("displayName", payload.get("display_name"))
    )
    if display_name is None and existing is not None:
        display_name = existing.connection.display_name
    if display_name is None:
        display_name = custom_slug

    custom_models = payload.get("customModels")
    if custom_models is None and existing is not None:
        custom_models = [
            {
                "id": model.id,
                "modelId": model.model_id,
                "displayName": model.display_name,
                "enabled": model.enabled,
            }
            for model in existing.models
            if model.source == "custom"
        ]
    models = normalize_custom_models(custom_models, existing)
    headers = normalize_headers(payload.get("headers"), existing)
    preferred_model_id = _normalize_optional_string(
        payload.get("preferredModelId", payload.get("preferred_model_id"))
    )
    if preferred_model_id is None and existing is not None:
        preferred_model_id = existing.connection.preferred_model_id

    return {
        "id": provider_id,
        "kind": "custom",
        "runtime_provider": "custom",
        "catalog_provider_id": None,
        "custom_slug": custom_slug,
        "display_name": display_name,
        "base_url": base_url,
        "secret_ref": existing.connection.secret_ref
        if existing
        else f"sec_{provider_id or custom_slug}",
        "api_key_input": payload.get(
            "apiKey", payload.get("api_key", _API_KEY_SENTINEL)
        ),
        "api_key_configured": resolve_api_key_configured(payload, existing),
        "health_status": payload.get("healthStatus", payload.get("health_status"))
        if ("healthStatus" in payload or "health_status" in payload)
        else (existing.connection.health_status if existing else "unknown"),
        "health_message": payload.get("healthMessage", payload.get("health_message"))
        if ("healthMessage" in payload or "health_message" in payload)
        else (existing.connection.health_message if existing else None),
        "last_validated_at": payload.get(
            "lastValidatedAt", payload.get("last_validated_at")
        )
        if ("lastValidatedAt" in payload or "last_validated_at" in payload)
        else (existing.connection.last_validated_at if existing else None),
        "model_policy": "custom_only",
        "preferred_model_id": preferred_model_id,
        "is_enabled": payload.get("isEnabled", payload.get("is_enabled", True))
        is not False,
        "models": models,
        "headers": headers,
    }


def normalize_custom_models(
    custom_models: Any, existing: ProviderConnectionBundle | None
) -> list[dict[str, Any]]:
    if custom_models is None:
        return []
    if not isinstance(custom_models, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="customModels must be a list",
        )

    result: list[dict[str, Any]] = []
    for index, model in enumerate(custom_models):
        if not isinstance(model, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid custom model payload",
            )
        model_id = _normalize_optional_string(
            model.get("modelId", model.get("model_id"))
        )
        if model_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Custom model id is required",
            )
        existing_model = _find_existing_model(existing, "custom", model_id)
        result.append(
            {
                "id": model.get("id")
                or (existing_model.id if existing_model else None),
                "source": "custom",
                "model_id": model_id,
                "display_name": _normalize_optional_string(
                    model.get("displayName", model.get("display_name"))
                )
                or model_id,
                "enabled": model.get("enabled", True) is not False,
                "sort_order": int(
                    model.get("sortOrder", model.get("sort_order", index))
                ),
            }
        )
    return result


def normalize_headers(
    headers_payload: Any, existing: ProviderConnectionBundle | None
) -> list[dict[str, Any]]:
    if headers_payload is None:
        if existing is None:
            return []
        return [
            {
                "id": header.id,
                "name": header.name,
                "value_kind": header.value_kind,
                "plain_value": header.plain_value,
                "sort_order": header.sort_order,
                "secret_value": _SECRET_VALUE_SENTINEL,
            }
            for header in existing.headers
        ]

    if not isinstance(headers_payload, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="headers must be a list"
        )

    result: list[dict[str, Any]] = []
    for index, header in enumerate(headers_payload):
        if not isinstance(header, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid header payload"
            )

        name = _normalize_optional_string(header.get("name"))
        if name is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Header name is required",
            )

        value_kind = str(header.get("valueKind", header.get("value_kind")) or "plain")
        if value_kind not in {"plain", "secret"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid header valueKind",
            )

        header_id = _normalize_optional_string(header.get("id"))
        plain_value = None
        secret_value = _SECRET_VALUE_SENTINEL
        if value_kind == "plain":
            plain_value = (
                _normalize_optional_string(
                    header.get("plainValue", header.get("plain_value"))
                )
                or ""
            )
        else:
            secret_value = header.get(
                "secretValue", header.get("secret_value", _SECRET_VALUE_SENTINEL)
            )

        result.append(
            {
                "id": header_id,
                "name": name,
                "value_kind": value_kind,
                "plain_value": plain_value,
                "sort_order": int(
                    header.get("sortOrder", header.get("sort_order", index))
                ),
                "secret_value": secret_value,
            }
        )
    return result


def build_secret_payload(
    normalized: dict[str, Any], existing_secret: ProviderSecretPayload
) -> ProviderSecretPayload:
    raw_api_key = normalized.get("api_key_input", _API_KEY_SENTINEL)
    if raw_api_key is _API_KEY_SENTINEL:
        api_key = existing_secret.api_key
    else:
        api_key = _normalize_optional_string(raw_api_key)

    secret_headers = dict(existing_secret.secret_headers)
    next_secret_headers: dict[str, str] = {}
    for header in normalized["headers"]:
        header_id = str(header["id"] or "")
        if header["value_kind"] != "secret":
            continue
        secret_value = header.get("secret_value", _SECRET_VALUE_SENTINEL)
        if secret_value is _SECRET_VALUE_SENTINEL:
            if header_id in secret_headers:
                next_secret_headers[header_id] = secret_headers[header_id]
            continue

        normalized_secret = _normalize_optional_string(secret_value)
        if normalized_secret is not None:
            next_secret_headers[header_id] = normalized_secret

    return ProviderSecretPayload(
        version=1, api_key=api_key, secret_headers=next_secret_headers
    )


def resolve_api_key_configured(
    payload: dict[str, Any], existing: ProviderConnectionBundle | None
) -> bool:
    if "apiKey" in payload:
        return _normalize_optional_string(payload.get("apiKey")) is not None
    if "api_key" in payload:
        return _normalize_optional_string(payload.get("api_key")) is not None
    return existing.connection.api_key_configured if existing is not None else False


def serialize_bundle(
    bundle: ProviderConnectionBundle, secret_store: LocalSecretStore
) -> dict[str, Any]:
    secret_payload = secret_store.load_provider_secret(bundle.connection.secret_ref)
    secret_headers = secret_payload.secret_headers if secret_payload else {}

    return {
        "id": bundle.connection.id,
        "kind": bundle.connection.kind,
        "runtimeProvider": bundle.connection.runtime_provider,
        "catalogProviderId": bundle.connection.catalog_provider_id,
        "customSlug": bundle.connection.custom_slug,
        "displayName": bundle.connection.display_name,
        "baseUrl": bundle.connection.base_url,
        "apiKeyConfigured": bundle.connection.api_key_configured,
        "healthStatus": bundle.connection.health_status,
        "healthMessage": bundle.connection.health_message,
        "lastValidatedAt": bundle.connection.last_validated_at,
        "modelPolicy": bundle.connection.model_policy,
        "preferredModelId": bundle.connection.preferred_model_id,
        "enabledModelIds": [
            model.model_id
            for model in bundle.models
            if model.source == "catalog" and model.enabled
        ],
        "customModels": [
            serialize_custom_model(model)
            for model in bundle.models
            if model.source == "custom"
        ],
        "headers": [
            serialize_header(header, secret_headers) for header in bundle.headers
        ],
        "updatedAt": bundle.connection.updated_at,
    }


def _health_status_from_error_code(error_code: str) -> str:
    if error_code in {"unauthorized", "missing_api_key"}:
        return "invalid_key"
    if error_code in {"timeout", "provider_timeout"}:
        return "timeout"
    if error_code in {"model_unavailable", "invalid_model"}:
        return "model_unavailable"
    return "invalid_config"


def serialize_custom_model(model: ProviderModelRecord) -> dict[str, Any]:
    return {
        "id": model.id,
        "modelId": model.model_id,
        "displayName": model.display_name or model.model_id,
        "enabled": model.enabled,
    }


def serialize_catalog_model_option(
    *,
    connection_id: str,
    provider_label: str,
    provider_id: str,
    model: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "connectionId": connection_id,
        "providerLabel": provider_label,
        "providerId": provider_id,
        "modelId": str(model.get("id") or ""),
        "label": str(model.get("id") or ""),
        "supportsReasoningTokens": bool(model.get("supportsReasoningTokens", False)),
    }
    reasoning_effort_values = model.get("reasoningEffortValues")
    if isinstance(reasoning_effort_values, list) and reasoning_effort_values:
        payload["reasoningEffortValues"] = [
            str(item) for item in reasoning_effort_values
        ]
    return payload


def serialize_custom_model_option(
    *,
    connection_id: str,
    provider_label: str,
    provider_id: str,
    model: ProviderModelRecord,
) -> dict[str, Any]:
    return {
        "connectionId": connection_id,
        "providerLabel": provider_label,
        "providerId": provider_id,
        "modelId": model.model_id,
        "label": model.display_name or model.model_id,
        "supportsReasoningTokens": False,
    }


def select_default_model(
    groups: list[dict[str, Any]], preferred_by_connection: dict[str, str]
) -> dict[str, str] | None:
    for group in groups:
        connection_id = str(group.get("connectionId") or "")
        models = group.get("models")
        if not connection_id or not isinstance(models, list) or not models:
            continue

        preferred_model_id = preferred_by_connection.get(connection_id)
        if preferred_model_id and any(
            str(model.get("modelId") or "") == preferred_model_id for model in models
        ):
            return {
                "connectionId": connection_id,
                "modelId": preferred_model_id,
            }

    for group in groups:
        connection_id = str(group.get("connectionId") or "")
        models = group.get("models")
        if not connection_id or not isinstance(models, list) or not models:
            continue

        first_model_id = str(models[0].get("modelId") or "")
        if first_model_id:
            return {
                "connectionId": connection_id,
                "modelId": first_model_id,
            }

    return None


def serialize_header(
    header: ProviderHeaderRecord, secret_headers: dict[str, str]
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": header.id,
        "name": header.name,
        "valueKind": header.value_kind,
    }
    if header.value_kind == "plain":
        payload["valuePreview"] = header.plain_value or ""
    else:
        payload["hasStoredSecret"] = header.id in secret_headers
    return payload


def _find_existing_model(
    existing: ProviderConnectionBundle | None, source: str, model_id: str
) -> ProviderModelRecord | None:
    if existing is None:
        return None
    for model in existing.models:
        if model.source == source and model.model_id == model_id:
            return model
    return None


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="enabledModelIds must be a list",
        )
    result: list[str] = []
    for item in value:
        normalized = _normalize_optional_string(item)
        if normalized is not None:
            result.append(normalized)
    return result


_API_KEY_SENTINEL = object()
_SECRET_VALUE_SENTINEL = object()
_VALUE_UNSET = object()
