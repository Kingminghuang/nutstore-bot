from __future__ import annotations

from types import SimpleNamespace

from ._support import _build_services, _print_json


def handle_providers_command(args: SimpleNamespace) -> int:
    database, _repositories, _secret_store, provider_service = _build_services(
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

        if args.providers_command == "delete":
            provider_service.delete_provider(args.provider_id)
            _print_json({"ok": True, "deletedProviderId": args.provider_id})
            return 0

        raise ValueError(f"Unknown providers command: {args.providers_command}")
    finally:
        database.close()