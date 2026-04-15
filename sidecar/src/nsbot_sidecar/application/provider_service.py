from __future__ import annotations

from dataclasses import dataclass
import logging
import sqlite3
from typing import Any

from fastapi import HTTPException, status

from nsbot_sidecar.providers.provider_catalog import BUILTIN_PROVIDERS, catalog_version, list_providers
from nsbot_sidecar.api.redaction import redact_sensitive
from nsbot_sidecar.domain.sensitive_write_guard import detect_sensitive_write_issues
from nsbot_sidecar.infrastructure.repositories import (
    ProviderConnectionBundle,
    ProviderConnectionsRepository,
    ProviderModelRecord,
)
from nsbot_sidecar.infrastructure.secret_store import LocalSecretStore, ProviderSecretPayload


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderService:
    repositories: ProviderConnectionsRepository
    secret_store: LocalSecretStore

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
            if not connection.is_enabled or not connection.api_key_configured:
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

        try:
            self.repositories.delete_by_id(provider_id)
        except sqlite3.IntegrityError as exc:
            # Keep historical session foreign keys intact; deleting an in-use
            # provider should be surfaced as a user-facing conflict, not a 500.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Provider is still referenced by sessions. "
                    "Please migrate or delete related sessions before removing it."
                ),
            ) from exc
        self.secret_store.delete_provider_secret(bundle.connection.secret_ref)

    def _upsert_provider(
        self,
        payload: dict[str, Any],
        *,
        existing: ProviderConnectionBundle | None,
    ) -> ProviderConnectionBundle:
        normalized = normalize_provider_payload(payload, existing)

        existing_secret = (
            self.secret_store.load_provider_secret(existing.connection.secret_ref)
            if existing is not None
            else None
        ) or ProviderSecretPayload(version=1, api_key=None)

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
                "model_policy": normalized["model_policy"],
                "preferred_model_id": normalized.get("preferred_model_id"),
                "is_enabled": normalized["is_enabled"],
            },
            models=normalized["models"],
        )
        self.secret_store.save_provider_secret(
            bundle.connection.secret_ref, secret_payload
        )
        return bundle

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
            "model_policy": model_policy,
            "preferred_model_id": preferred_model_id,
            "is_enabled": payload.get("isEnabled", payload.get("is_enabled", True))
            is not False,
            "models": models,
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
        "model_policy": "custom_only",
        "preferred_model_id": preferred_model_id,
        "is_enabled": payload.get("isEnabled", payload.get("is_enabled", True))
        is not False,
        "models": models,
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


def build_secret_payload(
    normalized: dict[str, Any], existing_secret: ProviderSecretPayload
) -> ProviderSecretPayload:
    raw_api_key = normalized.get("api_key_input", _API_KEY_SENTINEL)
    if raw_api_key is _API_KEY_SENTINEL:
        api_key = existing_secret.api_key
    else:
        api_key = _normalize_optional_string(raw_api_key)

    return ProviderSecretPayload(version=1, api_key=api_key)


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
    return {
        "id": bundle.connection.id,
        "kind": bundle.connection.kind,
        "runtimeProvider": bundle.connection.runtime_provider,
        "catalogProviderId": bundle.connection.catalog_provider_id,
        "customSlug": bundle.connection.custom_slug,
        "displayName": bundle.connection.display_name,
        "baseUrl": bundle.connection.base_url,
        "apiKeyConfigured": bundle.connection.api_key_configured,
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
        "updatedAt": bundle.connection.updated_at,
    }


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
_VALUE_UNSET = object()
