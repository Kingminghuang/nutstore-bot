from __future__ import annotations

from types import SimpleNamespace

from ._support import (
    _build_services,
    _find_provider_and_model_from_identity,
    _find_provider_id_by_model_id,
    _normalize_provider_ref,
    _print_json,
)


def handle_models_command(args: SimpleNamespace) -> int:
    database, repositories, _secret_store, provider_service = _build_services(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        if args.models_command == "create":
            name = str(args.name or "").strip()
            base_url = str(args.base_url or "").strip()
            model_id = str(args.model_id or "").strip()
            api_key = str(args.api_key or "").strip()
            if name == "":
                raise ValueError("Model provider name is required")
            if base_url == "":
                raise ValueError("Base URL is required")
            if model_id == "":
                raise ValueError("Model id is required")
            if api_key == "":
                raise ValueError("API key is required")

            payload = provider_service.add_custom_model(
                provider_id=name,
                base_url=base_url,
                api_key=api_key,
                model_id=model_id,
                provider_display_name=name,
                model_display_name=model_id,
            )
            _print_json(payload)
            return 0

        if args.models_command == "list":
            payload = provider_service.model_options_payload()
            groups = payload.get("groups")
            filtered = []
            provider_filter = str(
                args.provider_id or getattr(args, "provider", "") or ""
            ).strip()
            for group in groups if isinstance(groups, list) else []:
                provider_id = str(group.get("providerId") or "")
                if provider_filter and provider_filter != provider_id:
                    continue
                filtered.append(group)
            _print_json({"groups": filtered})
            return 0

        if args.models_command == "get":
            identity = str(args.identity or "").strip()
            if identity == "":
                raise ValueError("Model identity is required")
            model_options = provider_service.model_options_payload()
            provider_id, model_id = _find_provider_and_model_from_identity(
                model_options,
                identity=identity,
            )
            bundle = repositories.providers.get_bundle_by_id(provider_id)
            if bundle is None:
                raise ValueError(f"Provider not found: {provider_id}")

            _print_json(
                {
                    "id": f"{provider_id}:{model_id}",
                    "providerId": provider_id,
                    "modelId": model_id,
                    "provider": {
                        "id": bundle.provider.id,
                        "displayName": bundle.provider.display_name,
                        "runtimeProvider": bundle.provider.runtime_provider,
                        "baseUrl": bundle.provider.base_url,
                    },
                }
            )
            return 0

        if args.models_command == "set-default":
            identity = str(args.identity or "").strip()
            if identity == "":
                raise ValueError("Model identity is required")
            model_options = provider_service.model_options_payload()
            provider_id, model_id = _find_provider_and_model_from_identity(
                model_options,
                identity=identity,
            )

            bundle = repositories.providers.get_bundle_by_id(provider_id)
            if bundle is None:
                raise ValueError(f"Provider not found: {provider_id}")

            provider_service.set_default_model(provider_id, model_id)
            _print_json(
                {
                    "ok": True,
                    "providerId": provider_id,
                    "modelId": model_id,
                    "action": "set-default",
                    "providerRef": _normalize_provider_ref(
                        {
                            "catalogProviderId": bundle.provider.catalog_provider_id,
                            "customSlug": bundle.provider.custom_slug,
                            "runtimeProvider": bundle.provider.runtime_provider,
                        }
                    ),
                }
            )
            return 0

        model_id = str(args.model or "").strip()
        provider_id = str(args.provider_id or "").strip()
        if args.models_command == "remove" and provider_id == "":
            if model_id == "":
                raise ValueError("Model id is required")
            model_options = provider_service.model_options_payload()
            provider_id = _find_provider_id_by_model_id(
                model_options,
                model_id=model_id,
            )

        if provider_id == "":
            raise ValueError("Provider id is required")

        bundle = repositories.providers.get_bundle_by_id(provider_id)
        if bundle is None:
            raise ValueError(f"Provider not found: {provider_id}")

        if args.models_command == "remove":
            if bundle.provider.catalog_provider_id is not None:
                raise ValueError("models remove is only supported for custom providers")
            if not any(model.model_id == model_id for model in bundle.models):
                raise ValueError(f"Model '{model_id}' not found in provider '{provider_id}'")
            provider_service.remove_model(provider_id, model_id)
            _print_json(
                {
                    "ok": True,
                    "providerId": provider_id,
                    "modelId": model_id,
                    "action": "removed",
                }
            )
            return 0

        raise ValueError(f"Unknown models command: {args.models_command}")
    finally:
        database.close()