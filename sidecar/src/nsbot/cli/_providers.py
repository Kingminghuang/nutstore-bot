from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from nsbot.application.provider_service import serialize_bundle
from nsbot.infrastructure.secret_store import ProviderSecretPayload
from nsbot.providers.provider_catalog import BUILTIN_PROVIDERS, list_providers

from ._support import _build_services, _print_json


def _get_builtin_provider(provider_id: str) -> dict[str, Any]:
    normalized_id = str(provider_id or "").strip()
    if normalized_id == "":
        raise ValueError("Provider id is required")
    if normalized_id not in BUILTIN_PROVIDERS:
        raise ValueError(f"Unknown builtin provider: {normalized_id}")

    for provider in list_providers():
        if str(provider.get("id") or "") == normalized_id:
            if str(provider.get("kind") or "") != "builtin":
                break
            return provider

    raise ValueError(f"Unknown builtin provider: {normalized_id}")


def _serialize_provider_models(models: list[dict[str, object]]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for model in models:
        model_id = str(model.get("modelId") or model.get("id") or "").strip()
        if model_id == "":
            continue
        payload.append(
            {
                "modelId": model_id,
                "displayName": str(model.get("displayName") or model_id),
            }
        )
    return payload


def _catalog_models_payload(provider: dict[str, Any]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for model in provider.get("models", []):
        model_id = str(model.get("id") or "").strip()
        if model_id == "":
            continue
        payload.append(
            {
                "modelId": model_id,
                "displayName": str(model.get("displayName") or model_id),
            }
        )
    return payload


def _bundle_payload(bundle: Any) -> dict[str, Any]:
    payload = serialize_bundle(bundle)
    payload["models"] = _serialize_provider_models(payload.get("customModels", []))
    return payload


def handle_providers_command(args: SimpleNamespace) -> int:
    database, repositories, secret_store, provider_service = _build_services(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        if args.providers_command == "list":
            catalog = provider_service.catalog_payload()
            providers = provider_service.list_providers_payload()
            _print_json(
                {
                    "providers": catalog.get("providers", []),
                    "configuredProviders": providers.get("providers", []),
                }
            )
            return 0

        if args.providers_command == "create":
            catalog_provider = _get_builtin_provider(args.id)
            bundle = repositories.providers.save_bundle(
                provider_data={
                    "id": str(catalog_provider.get("id") or "").strip(),
                    "runtime_provider": str(
                        catalog_provider.get("runtimeProvider")
                        or catalog_provider.get("id")
                        or "custom"
                    ),
                    "catalog_provider_id": str(catalog_provider.get("id") or "").strip(),
                    "display_name": str(
                        catalog_provider.get("label")
                        or catalog_provider.get("id")
                        or ""
                    ).strip(),
                    "base_url": catalog_provider.get("baseUrl"),
                    "preferred_model_id": None,
                },
                models=_catalog_models_payload(catalog_provider),
            )
            secret_store.save_provider_secret(
                bundle.provider.secret_ref,
                ProviderSecretPayload(version=1, api_key=args.api_key),
            )
            _print_json(_bundle_payload(bundle))
            return 0

        if args.providers_command == "get":
            bundle = repositories.providers.get_bundle_by_id_or_raise(args.id)
            _print_json(_bundle_payload(bundle))
            return 0

        if args.providers_command == "delete":
            provider_service.delete_provider(args.provider_id)
            _print_json({"ok": True, "deletedProviderId": args.provider_id})
            return 0

        raise ValueError(f"Unknown providers command: {args.providers_command}")
    finally:
        database.close()