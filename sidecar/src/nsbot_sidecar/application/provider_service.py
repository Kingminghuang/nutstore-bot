from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from fastapi import HTTPException, status

from nsbot_sidecar.api.redaction import redact_sensitive
from nsbot_sidecar.domain.sensitive_write_guard import detect_sensitive_write_issues
from nsbot_sidecar.infrastructure.repositories import (
    DefaultModelSelectionRepository,
    ProviderBundle,
    ProviderModelRecord,
    ProvidersRepository,
)
from nsbot_sidecar.infrastructure.secret_store import LocalSecretStore, ProviderSecretPayload
from nsbot_sidecar.providers.provider_catalog import BUILTIN_PROVIDERS, catalog_version, list_providers


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderService:
    repositories: ProvidersRepository
    default_model_selection: DefaultModelSelectionRepository
    secret_store: LocalSecretStore

    def catalog_payload(self) -> dict[str, Any]:
        providers = list_providers()
        providers.append(custom_provider_template())
        return {"version": catalog_version(providers), "providers": providers}

    def list_providers_payload(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "providers": [
                serialize_bundle(bundle) for bundle in self.repositories.list_bundles()
            ]
        }

    def model_options_payload(self) -> dict[str, Any]:
        groups, preferred_by_provider = self._build_model_groups()
        return {
            "groups": groups,
            "defaultSelection": self.resolve_default_selection(
                groups, preferred_by_provider
            ),
        }

    def create_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        bundle = self._upsert_provider(payload, existing=None)
        return serialize_bundle(bundle)

    def update_provider(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self.repositories.get_bundle_by_id(provider_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider not found",
            )
        merged = dict(payload)
        merged["id"] = provider_id
        bundle = self._upsert_provider(merged, existing=existing)
        LOGGER.info(
            "Provider updated: provider_id=%s runtime_provider=%s display_name=%s",
            bundle.provider.id,
            bundle.provider.runtime_provider,
            bundle.provider.display_name,
        )
        return serialize_bundle(bundle)

    def delete_provider(self, provider_id: str) -> None:
        bundle = self.repositories.get_bundle_by_id(provider_id)
        if bundle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider not found",
            )

        default_selection = self.default_model_selection.get()
        self.repositories.delete_by_id(provider_id)
        if default_selection is not None and default_selection.provider_id == provider_id:
            self.default_model_selection.clear()
        self.secret_store.delete_provider_secret(bundle.provider.secret_ref)

    def add_custom_model(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str,
        model_id: str,
        provider_display_name: str | None = None,
        model_display_name: str | None = None,
    ) -> dict[str, Any]:
        existing = self.repositories.get_bundle_by_id(provider_id)
        if existing is None:
            bundle = self._upsert_provider(
                {
                    "kind": "custom",
                    "customSlug": provider_id,
                    "displayName": provider_display_name or provider_id,
                    "baseUrl": base_url,
                    "apiKey": api_key,
                    "customModels": [
                        {
                            "modelId": model_id,
                            "displayName": model_display_name or model_id,
                        }
                    ],
                    "preferredModelId": model_id,
                },
                existing=None,
            )
            return serialize_bundle(bundle)

        if existing.provider.catalog_provider_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="models create cannot append models to builtin providers",
            )

        next_models = [
            {
                "id": model.id,
                "modelId": model.model_id,
                "displayName": model.display_name,
            }
            for model in existing.models
        ]
        if any(str(item.get("modelId") or "") == model_id for item in next_models):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Model already exists for provider",
            )
        next_models.append(
            {
                "modelId": model_id,
                "displayName": model_display_name or model_id,
            }
        )
        bundle = self._upsert_provider(
            {
                "id": existing.provider.id,
                "kind": "custom",
                "customSlug": existing.provider.id,
                "displayName": provider_display_name or existing.provider.display_name,
                "baseUrl": base_url,
                "apiKey": api_key,
                "customModels": next_models,
                "preferredModelId": existing.provider.preferred_model_id,
            },
            existing=existing,
        )
        return serialize_bundle(bundle)

    def remove_model(self, provider_id: str, model_id: str) -> None:
        bundle = self.repositories.get_bundle_by_id(provider_id)
        if bundle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider not found",
            )
        if bundle.provider.catalog_provider_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="models remove is only supported for custom providers",
            )

        removed = self.repositories.delete_model(provider_id, model_id)
        if not removed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Model not found",
            )

        refreshed = self.repositories.get_bundle_by_id_or_raise(provider_id)
        preferred_model_id = refreshed.provider.preferred_model_id
        if preferred_model_id == model_id:
            next_preferred = refreshed.models[0].model_id if refreshed.models else None
            self.repositories.save_bundle(
                provider_data={
                    "id": refreshed.provider.id,
                    "runtime_provider": refreshed.provider.runtime_provider,
                    "catalog_provider_id": refreshed.provider.catalog_provider_id,
                    "display_name": refreshed.provider.display_name,
                    "base_url": refreshed.provider.base_url,
                    "secret_ref": refreshed.provider.secret_ref,
                    "preferred_model_id": next_preferred,
                },
                models=[
                    {
                        "id": model.id,
                        "model_id": model.model_id,
                        "display_name": model.display_name,
                    }
                    for model in refreshed.models
                ],
            )

        default_selection = self.default_model_selection.get()
        if (
            default_selection is not None
            and default_selection.provider_id == provider_id
            and default_selection.model_id == model_id
        ):
            self.default_model_selection.clear()

    def set_default_model(self, provider_id: str, model_id: str) -> None:
        groups, _preferred_by_provider = self._build_model_groups()
        if not any(
            str(group.get("providerId") or "") == provider_id
            and any(str(model.get("modelId") or "") == model_id for model in group.get("models", []))
            for group in groups
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Model not found",
            )
        self.default_model_selection.set(provider_id, model_id)

    def resolve_default_selection(
        self,
        groups: list[dict[str, Any]],
        preferred_by_provider: dict[str, str],
    ) -> dict[str, str] | None:
        stored = self.default_model_selection.get()
        if stored is not None and _group_has_model(groups, stored.provider_id, stored.model_id):
            return {"providerId": stored.provider_id, "modelId": stored.model_id}

        for group in groups:
            provider_id = str(group.get("providerId") or "")
            preferred_model_id = preferred_by_provider.get(provider_id)
            if preferred_model_id and _group_has_model(groups, provider_id, preferred_model_id):
                return {"providerId": provider_id, "modelId": preferred_model_id}

        for group in groups:
            provider_id = str(group.get("providerId") or "")
            models = group.get("models")
            if not provider_id or not isinstance(models, list) or not models:
                continue
            model_id = str(models[0].get("modelId") or "")
            if model_id:
                return {"providerId": provider_id, "modelId": model_id}
        return None

    def _upsert_provider(
        self,
        payload: dict[str, Any],
        *,
        existing: ProviderBundle | None,
    ) -> ProviderBundle:
        normalized = normalize_provider_payload(payload, existing)
        existing_secret = (
            self.secret_store.load_provider_secret(existing.provider.secret_ref)
            if existing is not None
            else None
        ) or ProviderSecretPayload(version=1, api_key=None)

        secret_ref = (
            existing.provider.secret_ref
            if existing is not None
            else normalized["secret_ref"]
        )
        secret_payload = build_secret_payload(normalized, existing_secret)
        if secret_payload.api_key is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="API key is required",
            )

        sensitive_issues = detect_sensitive_write_issues(
            {
                "provider_data": normalized,
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
            provider_data={
                "id": normalized["id"],
                "runtime_provider": normalized["runtime_provider"],
                "catalog_provider_id": normalized.get("catalog_provider_id"),
                "display_name": normalized["display_name"],
                "base_url": normalized.get("base_url"),
                "secret_ref": secret_ref,
                "preferred_model_id": normalized.get("preferred_model_id"),
            },
            models=normalized["models"],
        )
        self.secret_store.save_provider_secret(bundle.provider.secret_ref, secret_payload)
        return bundle

    def _build_model_groups(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        catalog_entries = list_providers()
        catalog_by_id = {str(entry["id"]): entry for entry in catalog_entries}
        groups: list[dict[str, Any]] = []
        preferred_by_provider: dict[str, str] = {}

        for bundle in self.repositories.list_bundles():
            provider = bundle.provider
            provider_name = provider.catalog_provider_id or provider.id
            models: list[dict[str, Any]] = []

            if provider.catalog_provider_id is not None:
                catalog_entry = catalog_by_id.get(provider.catalog_provider_id)
                if catalog_entry is None:
                    continue
                models = [
                    serialize_catalog_model_option(
                        provider_label=provider.display_name,
                        provider_id=provider.id,
                        model=model,
                    )
                    for model in list(catalog_entry.get("models", []))
                ]
            else:
                models = [
                    serialize_custom_model_option(
                        provider_label=provider.display_name,
                        provider_id=provider.id,
                        model=model,
                    )
                    for model in bundle.models
                    if model.model_id
                ]

            if not models:
                continue
            if provider.preferred_model_id:
                preferred_by_provider[provider.id] = provider.preferred_model_id
            groups.append(
                {
                    "providerId": provider.id,
                    "providerLabel": provider.display_name,
                    "providerName": provider_name,
                    "models": models,
                }
            )

        return groups, preferred_by_provider


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
    payload: dict[str, Any], existing: ProviderBundle | None
) -> dict[str, Any]:
    kind = _resolve_kind(payload, existing)
    provider_id = _resolve_provider_id(payload, existing, kind)
    if provider_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider id could not be resolved",
        )

    if kind == "builtin":
        catalog_provider_id = _normalize_optional_string(
            payload.get("catalogProviderId", payload.get("catalog_provider_id"))
            or (existing.provider.catalog_provider_id if existing else None)
        )
        if catalog_provider_id not in BUILTIN_PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown builtin provider",
            )
        catalog_entry = next(
            provider for provider in list_providers() if str(provider.get("id") or "") == catalog_provider_id
        )
        display_name = _normalize_optional_string(
            payload.get("displayName", payload.get("display_name"))
        ) or (existing.provider.display_name if existing else str(catalog_entry["label"]))
        base_url_input = payload.get("baseUrl", payload.get("base_url"))
        if base_url_input is None and existing is not None:
            base_url_input = existing.provider.base_url
        base_url = _normalize_optional_string(base_url_input)
        if str(catalog_entry.get("baseUrlPolicy") or "") == "hidden":
            base_url = None
        preferred_model_id = _normalize_optional_string(
            payload.get("preferredModelId", payload.get("preferred_model_id"))
        )
        if preferred_model_id is None and existing is not None:
            preferred_model_id = existing.provider.preferred_model_id
        return {
            "id": provider_id,
            "runtime_provider": str(catalog_entry["runtimeProvider"]),
            "catalog_provider_id": catalog_provider_id,
            "display_name": display_name,
            "base_url": base_url,
            "secret_ref": existing.provider.secret_ref if existing else f"sec_{provider_id}",
            "api_key_input": payload.get("apiKey", payload.get("api_key", _API_KEY_SENTINEL)),
            "preferred_model_id": preferred_model_id,
            "models": [],
        }

    display_name = _normalize_optional_string(
        payload.get("displayName", payload.get("display_name"))
    ) or (existing.provider.display_name if existing else provider_id)
    base_url_value = payload.get("baseUrl", payload.get("base_url", _VALUE_UNSET))
    if base_url_value is _VALUE_UNSET and existing is not None:
        base_url_value = existing.provider.base_url
    base_url = None if base_url_value is _VALUE_UNSET else _normalize_optional_string(base_url_value)
    if base_url is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Base URL is required for custom providers",
        )
    custom_models = payload.get("customModels")
    if custom_models is None and existing is not None:
        custom_models = [
            {
                "id": model.id,
                "modelId": model.model_id,
                "displayName": model.display_name,
            }
            for model in existing.models
        ]
    preferred_model_id = _normalize_optional_string(
        payload.get("preferredModelId", payload.get("preferred_model_id"))
    )
    if preferred_model_id is None and existing is not None:
        preferred_model_id = existing.provider.preferred_model_id
    return {
        "id": provider_id,
        "runtime_provider": "custom",
        "catalog_provider_id": None,
        "display_name": display_name,
        "base_url": base_url,
        "secret_ref": existing.provider.secret_ref if existing else f"sec_{provider_id}",
        "api_key_input": payload.get("apiKey", payload.get("api_key", _API_KEY_SENTINEL)),
        "preferred_model_id": preferred_model_id,
        "models": normalize_custom_models(custom_models, existing),
    }


def normalize_custom_models(
    custom_models: Any, existing: ProviderBundle | None
) -> list[dict[str, Any]]:
    if custom_models is None:
        return []
    if not isinstance(custom_models, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="customModels must be a list",
        )
    result: list[dict[str, Any]] = []
    for model in custom_models:
        if not isinstance(model, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid custom model payload",
            )
        model_id = _normalize_optional_string(model.get("modelId", model.get("model_id")))
        if model_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Custom model id is required",
            )
        existing_model = _find_existing_model(existing, model_id)
        result.append(
            {
                "id": model.get("id") or (existing_model.id if existing_model else None),
                "model_id": model_id,
                "display_name": _normalize_optional_string(
                    model.get("displayName", model.get("display_name"))
                )
                or model_id,
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


def serialize_bundle(bundle: ProviderBundle) -> dict[str, Any]:
    return {
        "id": bundle.provider.id,
        "kind": bundle.provider.kind,
        "runtimeProvider": bundle.provider.runtime_provider,
        "catalogProviderId": bundle.provider.catalog_provider_id,
        "customSlug": bundle.provider.custom_slug,
        "displayName": bundle.provider.display_name,
        "baseUrl": bundle.provider.base_url,
        "apiKeyConfigured": True,
        "modelPolicy": bundle.provider.model_policy,
        "preferredModelId": bundle.provider.preferred_model_id,
        "enabledModelIds": [],
        "customModels": [serialize_custom_model(model) for model in bundle.models if model.model_id],
        "updatedAt": bundle.provider.updated_at,
    }


def serialize_custom_model(model: ProviderModelRecord) -> dict[str, Any]:
    return {
        "id": model.id,
        "modelId": model.model_id,
        "displayName": model.display_name or model.model_id,
        "enabled": True,
    }


def serialize_catalog_model_option(
    *,
    provider_label: str,
    provider_id: str,
    model: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "providerId": provider_id,
        "providerLabel": provider_label,
        "modelId": str(model.get("id") or ""),
        "label": str(model.get("id") or ""),
        "supportsReasoningTokens": bool(model.get("supportsReasoningTokens", False)),
    }
    reasoning_effort_values = model.get("reasoningEffortValues")
    if isinstance(reasoning_effort_values, list) and reasoning_effort_values:
        payload["reasoningEffortValues"] = [str(item) for item in reasoning_effort_values]
    return payload


def serialize_custom_model_option(
    *,
    provider_label: str,
    provider_id: str,
    model: ProviderModelRecord,
) -> dict[str, Any]:
    return {
        "providerId": provider_id,
        "providerLabel": provider_label,
        "modelId": model.model_id,
        "label": model.display_name or model.model_id,
        "supportsReasoningTokens": False,
    }


def _resolve_kind(payload: dict[str, Any], existing: ProviderBundle | None) -> str:
    explicit = _normalize_optional_string(payload.get("kind"))
    if explicit in {"builtin", "custom"}:
        return explicit
    if payload.get("catalogProviderId") is not None or payload.get("catalog_provider_id") is not None:
        return "builtin"
    if payload.get("customSlug") is not None or payload.get("custom_slug") is not None:
        return "custom"
    if existing is not None:
        return existing.provider.kind
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid provider kind",
    )


def _resolve_provider_id(
    payload: dict[str, Any], existing: ProviderBundle | None, kind: str
) -> str | None:
    explicit = _normalize_optional_string(payload.get("id"))
    if explicit is not None:
        return explicit
    if kind == "builtin":
        return _normalize_optional_string(
            payload.get("catalogProviderId", payload.get("catalog_provider_id"))
        ) or (existing.provider.id if existing else None)
    return _normalize_optional_string(
        payload.get("customSlug", payload.get("custom_slug"))
    ) or (existing.provider.id if existing else None)


def _find_existing_model(
    existing: ProviderBundle | None, model_id: str
) -> ProviderModelRecord | None:
    if existing is None:
        return None
    for model in existing.models:
        if model.model_id == model_id:
            return model
    return None


def _group_has_model(
    groups: list[dict[str, Any]], provider_id: str, model_id: str
) -> bool:
    for group in groups:
        if str(group.get("providerId") or "") != provider_id:
            continue
        models = group.get("models")
        if not isinstance(models, list):
            return False
        return any(str(model.get("modelId") or "") == model_id for model in models)
    return False


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_API_KEY_SENTINEL = object()
_VALUE_UNSET = object()