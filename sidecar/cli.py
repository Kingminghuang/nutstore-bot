import argparse
import json
import os
import sys
import uuid
from typing import Any

from local_paths import nsbot_home
from provider_service import ProviderService
from repositories import ProviderConnectionBundle, create_repositories
from runtime_service import CodeAgentRuntimeService, RunMetadata, RuntimeWorkerConfig
from secret_store import LocalSecretStore
from storage import connect_database


COMMANDS = {"run", "providers", "models", "help"}


def _normalize_provider_ref(bundle: dict[str, Any]) -> str:
    return str(
        bundle.get("catalogProviderId")
        or bundle.get("customSlug")
        or bundle.get("runtimeProvider")
        or ""
    )


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NSBot CLI for provider/model management and runtime execution"
    )
    parser.add_argument(
        "--ns-bot-home",
        type=str,
        default=str(nsbot_home()),
        help="Path to NSBot data directory.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    providers = subparsers.add_parser("providers", help="Manage provider connections")
    providers_sub = providers.add_subparsers(dest="providers_command", required=True)
    providers_sub.add_parser("list", help="List catalog providers and connections")

    providers_use = providers_sub.add_parser(
        "use", help="Set preferred model for provider/connection"
    )
    providers_use.add_argument(
        "provider", type=str, help="Provider id or connection id"
    )
    providers_use.add_argument(
        "--model",
        type=str,
        default="",
        help="Preferred model id. If omitted, use first available model.",
    )

    providers_delete = providers_sub.add_parser(
        "delete", help="Delete a provider connection"
    )
    providers_delete.add_argument(
        "--connection-id", type=str, required=True, help="Provider connection id"
    )

    models = subparsers.add_parser("models", help="Manage model options")
    models_sub = models.add_subparsers(dest="models_command", required=True)

    models_list = models_sub.add_parser("list", help="List model options")
    models_list.add_argument(
        "--provider", type=str, default="", help="Filter by provider id"
    )
    models_list.add_argument(
        "--connection-id", type=str, default="", help="Filter by connection id"
    )
    models_sub.add_parser("status", help="Show current default selection")

    models_enable = models_sub.add_parser("enable", help="Enable a model")
    models_enable.add_argument("--connection-id", type=str, required=True)
    models_enable.add_argument("--model", type=str, required=True)

    models_disable = models_sub.add_parser("disable", help="Disable a model")
    models_disable.add_argument("--connection-id", type=str, required=True)
    models_disable.add_argument("--model", type=str, required=True)

    models_remove = models_sub.add_parser(
        "remove", help="Remove a custom model from a custom provider"
    )
    models_remove.add_argument("--connection-id", type=str, required=True)
    models_remove.add_argument("--model", type=str, required=True)

    run = subparsers.add_parser("run", help="Execute one task")
    run.add_argument("user_input", type=str, help="Task prompt")
    run.add_argument(
        "--run-id",
        type=str,
        default=str(uuid.uuid4()),
        help="Run ID (defaults to a new UUID)",
    )
    run.add_argument(
        "--workspace-path",
        type=str,
        default=os.getcwd(),
        help="Workspace directory path (default: current directory)",
    )
    run.add_argument(
        "--model-id",
        type=str,
        default="gpt-5.4",
        help="Primary model id for memory/title fallback",
    )
    run.add_argument(
        "--connection-id",
        type=str,
        default="",
        help="Use this provider connection id from database",
    )
    run.add_argument(
        "--selected-model-id",
        type=str,
        default="",
        help="Model id to pair with --connection-id",
    )
    run.add_argument(
        "--provider",
        type=str,
        default=os.getenv("PROVIDER", "").strip(),
        help="Runtime provider override",
    )
    run.add_argument(
        "--base-url",
        type=str,
        default=os.getenv("BASE_URL", "").strip(),
        help="Runtime base URL override",
    )
    run.add_argument(
        "--api-key",
        type=str,
        default=os.getenv("API_KEY", "").strip(),
        help="Runtime API key override",
    )
    run.add_argument(
        "--model",
        type=str,
        default=os.getenv("MODEL", "").strip(),
        help="Runtime model id override",
    )
    run.add_argument(
        "--request-timeout-ms",
        type=int,
        default=60000,
        help="Request timeout in milliseconds",
    )
    run.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="Maximum number of turns/steps",
    )
    run.add_argument(
        "--fd-executable",
        type=str,
        default="",
        help="Path to fd executable",
    )
    run.add_argument(
        "--rg-executable",
        type=str,
        default="",
        help="Path to rg executable",
    )
    run.add_argument(
        "--tool-os-type",
        type=str,
        default="",
        help="Target OS type for tooling",
    )
    run.add_argument(
        "--session-key",
        type=str,
        default="",
        help="Session key override",
    )
    run.add_argument(
        "--dump-result",
        action="store_true",
        help="Dump whole JSON result",
    )
    run.add_argument(
        "--diagnose",
        action="store_true",
        help="Resolve provider/model and print config only",
    )

    help_cmd = subparsers.add_parser("help", help="Show help for command/subcommand")
    help_cmd.add_argument(
        "topic",
        nargs="*",
        help="Command path, e.g. run or providers use",
    )
    return parser


def _ensure_run_compat(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if argv[0] == "help":
        return argv
    if any(token in {"-h", "--help"} for token in argv):
        return argv
    if any(token in COMMANDS for token in argv):
        return argv
    return ["run", *argv]


def _print_help_for_topic(parser: argparse.ArgumentParser, topic: list[str]) -> int:
    current = parser
    for token in topic:
        subparsers_action = None
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                subparsers_action = action
                break
        if subparsers_action is None:
            print(f"Unknown help topic: {' '.join(topic)}", file=sys.stderr)
            return 1
        next_parser = subparsers_action.choices.get(token)
        if next_parser is None:
            print(f"Unknown help topic: {' '.join(topic)}", file=sys.stderr)
            return 1
        current = next_parser
    current.print_help()
    return 0


def _build_services(ns_bot_home_value: str):
    database = connect_database(ns_bot_home_value)
    repositories = create_repositories(database)
    secret_store = LocalSecretStore(ns_bot_home_value)
    provider_service = ProviderService(
        repositories=repositories.providers,
        secret_store=secret_store,
    )
    return database, repositories, secret_store, provider_service


def _find_target_group(
    model_options: dict[str, Any], *, provider_ref: str
) -> dict[str, Any] | None:
    groups = model_options.get("groups")
    if not isinstance(groups, list):
        return None
    for group in groups:
        connection_id = str(group.get("connectionId") or "")
        provider_id = str(group.get("providerId") or "")
        if provider_ref == connection_id or provider_ref == provider_id:
            return group
    return None


def _require_connection(
    bundles: list[dict[str, Any]], connection_id: str
) -> dict[str, Any] | None:
    for connection in bundles:
        if str(connection.get("id") or "") == connection_id:
            return connection
    return None


def _first_model_id(group: dict[str, Any]) -> str | None:
    models = group.get("models")
    if not isinstance(models, list) or not models:
        return None
    model_id = str(models[0].get("modelId") or "").strip()
    return model_id or None


def _resolve_catalog_model_ids(bundle: ProviderConnectionBundle) -> list[str]:
    from provider_catalog import list_providers

    provider_id = bundle.connection.catalog_provider_id or ""
    for provider in list_providers():
        if str(provider.get("id") or "") != provider_id:
            continue
        models = provider.get("models")
        if not isinstance(models, list):
            return []
        return [str(model.get("id") or "") for model in models if model.get("id")]
    return []


def _handle_providers_command(args: argparse.Namespace) -> int:
    database, repositories, _secret_store, provider_service = _build_services(
        args.ns_bot_home
    )
    try:
        if args.providers_command == "list":
            catalog = provider_service.catalog_payload()
            connections = provider_service.list_connections_payload()
            _print_json(
                {
                    "providers": catalog.get("providers", []),
                    "connections": connections.get("connections", []),
                }
            )
            return 0

        if args.providers_command == "delete":
            provider_service.delete_provider(args.connection_id)
            _print_json({"ok": True, "deletedConnectionId": args.connection_id})
            return 0

        if args.providers_command == "use":
            target_ref = str(args.provider or "").strip()
            if target_ref == "":
                raise ValueError("Provider id or connection id is required")

            model_options = provider_service.model_options_payload()
            group = _find_target_group(model_options, provider_ref=target_ref)
            if group is None:
                raise ValueError(
                    f"No available connected provider found for '{target_ref}'"
                )

            connection_id = str(group.get("connectionId") or "")
            requested_model = str(args.model or "").strip()
            model_id = requested_model or _first_model_id(group)
            if model_id is None:
                raise ValueError(f"No available models for '{target_ref}'")

            models = group.get("models")
            allowed_ids = {
                str(model.get("modelId") or "")
                for model in (models if isinstance(models, list) else [])
            }
            if model_id not in allowed_ids:
                raise ValueError(
                    f"Model '{model_id}' is not available for connection '{connection_id}'"
                )

            updated = provider_service.update_provider(
                connection_id, {"preferredModelId": model_id}
            )
            _print_json(
                {
                    "ok": True,
                    "connectionId": connection_id,
                    "providerId": _normalize_provider_ref(updated),
                    "modelId": model_id,
                }
            )
            return 0

        raise ValueError(f"Unknown providers command: {args.providers_command}")
    finally:
        database.close()


def _handle_models_command(args: argparse.Namespace) -> int:
    database, repositories, _secret_store, provider_service = _build_services(
        args.ns_bot_home
    )
    try:
        if args.models_command == "list":
            payload = provider_service.model_options_payload()
            groups = payload.get("groups")
            filtered = []
            provider_filter = str(args.provider or "").strip()
            connection_filter = str(args.connection_id or "").strip()
            for group in groups if isinstance(groups, list) else []:
                provider_id = str(group.get("providerId") or "")
                connection_id = str(group.get("connectionId") or "")
                if provider_filter and provider_filter != provider_id:
                    continue
                if connection_filter and connection_filter != connection_id:
                    continue
                filtered.append(group)
            _print_json({"groups": filtered})
            return 0

        if args.models_command == "status":
            payload = provider_service.model_options_payload()
            default_selection = payload.get("defaultSelection")
            groups = payload.get("groups")
            default_group = None
            if isinstance(default_selection, dict):
                selected_connection = str(default_selection.get("connectionId") or "")
                for group in groups if isinstance(groups, list) else []:
                    if str(group.get("connectionId") or "") == selected_connection:
                        default_group = group
                        break
            _print_json(
                {
                    "defaultSelection": default_selection,
                    "defaultGroup": default_group,
                    "groupCount": len(groups) if isinstance(groups, list) else 0,
                }
            )
            return 0

        connection_id = str(args.connection_id or "").strip()
        model_id = str(args.model or "").strip()
        bundle = repositories.providers.get_bundle_by_id(connection_id)
        if bundle is None:
            raise ValueError(f"Provider connection not found: {connection_id}")

        if args.models_command == "remove":
            if bundle.connection.kind != "custom":
                raise ValueError(
                    "models remove is only supported for custom provider connections"
                )
            custom_models = [
                {
                    "id": model.id,
                    "modelId": model.model_id,
                    "displayName": model.display_name,
                    "enabled": model.enabled,
                }
                for model in bundle.models
                if model.source == "custom" and model.model_id != model_id
            ]
            if len(custom_models) == len(
                [model for model in bundle.models if model.source == "custom"]
            ):
                raise ValueError(
                    f"Model '{model_id}' not found in connection '{connection_id}'"
                )

            next_preferred = bundle.connection.preferred_model_id
            if next_preferred == model_id:
                replacement = next(
                    (
                        str(model.get("modelId") or "")
                        for model in custom_models
                        if bool(model.get("enabled", True))
                    ),
                    None,
                )
                next_preferred = replacement

            provider_service.update_provider(
                connection_id,
                {
                    "customModels": custom_models,
                    "preferredModelId": next_preferred,
                },
            )
            _print_json(
                {
                    "ok": True,
                    "connectionId": connection_id,
                    "modelId": model_id,
                    "action": "removed",
                }
            )
            return 0

        if bundle.connection.kind == "custom":
            custom_models = []
            found = False
            for model in bundle.models:
                if model.source != "custom":
                    continue
                enabled = model.enabled
                if model.model_id == model_id:
                    found = True
                    enabled = args.models_command == "enable"
                custom_models.append(
                    {
                        "id": model.id,
                        "modelId": model.model_id,
                        "displayName": model.display_name,
                        "enabled": enabled,
                    }
                )
            if not found:
                raise ValueError(
                    f"Model '{model_id}' not found in connection '{connection_id}'"
                )

            provider_service.update_provider(
                connection_id,
                {
                    "customModels": custom_models,
                    "preferredModelId": bundle.connection.preferred_model_id,
                },
            )
            _print_json(
                {
                    "ok": True,
                    "connectionId": connection_id,
                    "modelId": model_id,
                    "action": "enabled"
                    if args.models_command == "enable"
                    else "disabled",
                }
            )
            return 0

        catalog_ids = _resolve_catalog_model_ids(bundle)
        if model_id not in set(catalog_ids):
            raise ValueError(
                f"Model '{model_id}' is not a catalog model for connection '{connection_id}'"
            )

        if (
            args.models_command == "enable"
            and bundle.connection.model_policy != "restricted"
        ):
            _print_json(
                {
                    "ok": True,
                    "connectionId": connection_id,
                    "modelId": model_id,
                    "action": "enabled",
                    "modelPolicy": bundle.connection.model_policy,
                }
            )
            return 0

        if bundle.connection.model_policy == "restricted":
            enabled_set = {
                model.model_id
                for model in bundle.models
                if model.source == "catalog" and model.enabled
            }
        else:
            enabled_set = set(catalog_ids)

        if args.models_command == "enable":
            enabled_set.add(model_id)
        elif args.models_command == "disable":
            enabled_set.discard(model_id)

        enabled_in_order = [item for item in catalog_ids if item in enabled_set]
        provider_service.update_provider(
            connection_id,
            {
                "modelPolicy": "restricted",
                "enabledModelIds": enabled_in_order,
                "preferredModelId": bundle.connection.preferred_model_id,
            },
        )
        _print_json(
            {
                "ok": True,
                "connectionId": connection_id,
                "modelId": model_id,
                "action": "enabled" if args.models_command == "enable" else "disabled",
            }
        )
        return 0
    finally:
        database.close()


def _resolve_run_target(
    args: argparse.Namespace,
) -> tuple[RuntimeWorkerConfig, dict[str, Any]]:
    database, repositories, secret_store, provider_service = _build_services(
        args.ns_bot_home
    )
    try:
        direct_mode = any(
            [
                str(args.provider or "").strip(),
                str(args.base_url or "").strip(),
                str(args.api_key or "").strip(),
                str(args.model or "").strip(),
            ]
        )
        explicit_connection_id = str(args.connection_id or "").strip()
        explicit_selected_model = str(args.selected_model_id or "").strip()

        if direct_mode:
            model_id = str(args.model or args.model_id).strip()
            config = RuntimeWorkerConfig(
                model_id=model_id,
                provider=str(args.provider or "custom").strip() or "custom",
                base_url=str(args.base_url or "").strip() or None,
                api_key=str(args.api_key or "").strip() or None,
                model=model_id,
                request_timeout_ms=args.request_timeout_ms,
                ns_bot_home=args.ns_bot_home,
                workspace_path_default=args.workspace_path,
                fd_executable=args.fd_executable or None,
                rg_executable=args.rg_executable or None,
                tool_os_type=args.tool_os_type or None,
                max_steps=args.max_steps,
            )
            return (
                config,
                {
                    "mode": "direct",
                    "connectionId": None,
                    "providerId": str(config.provider or ""),
                    "runtimeProvider": str(config.provider or ""),
                    "modelId": model_id,
                    "baseUrl": config.base_url,
                    "hasApiKey": bool(config.api_key),
                },
            )

        if explicit_connection_id:
            bundle = repositories.providers.get_bundle_by_id(explicit_connection_id)
            if bundle is None:
                raise ValueError(
                    f"Provider connection not found: {explicit_connection_id}"
                )
            selected_model = (
                explicit_selected_model or bundle.connection.preferred_model_id
            )
            if not selected_model:
                options = provider_service.model_options_payload()
                group = _find_target_group(options, provider_ref=explicit_connection_id)
                if group is None:
                    raise ValueError(
                        f"No available models for connection '{explicit_connection_id}'"
                    )
                selected_model = _first_model_id(group)
                if selected_model is None:
                    raise ValueError(
                        f"No available models for connection '{explicit_connection_id}'"
                    )

            secret_payload = secret_store.load_provider_secret(
                bundle.connection.secret_ref
            )
            api_key = secret_payload.api_key if secret_payload is not None else None
            config = RuntimeWorkerConfig(
                model_id=str(selected_model),
                provider=bundle.connection.runtime_provider,
                base_url=bundle.connection.base_url,
                api_key=api_key,
                model=str(selected_model),
                request_timeout_ms=args.request_timeout_ms,
                ns_bot_home=args.ns_bot_home,
                workspace_path_default=args.workspace_path,
                fd_executable=args.fd_executable or None,
                rg_executable=args.rg_executable or None,
                tool_os_type=args.tool_os_type or None,
                max_steps=args.max_steps,
            )
            return (
                config,
                {
                    "mode": "connection",
                    "connectionId": bundle.connection.id,
                    "providerId": bundle.connection.catalog_provider_id
                    or bundle.connection.custom_slug
                    or bundle.connection.runtime_provider,
                    "runtimeProvider": bundle.connection.runtime_provider,
                    "modelId": str(selected_model),
                    "baseUrl": bundle.connection.base_url,
                    "hasApiKey": bool(api_key),
                    "healthStatus": bundle.connection.health_status,
                },
            )

        options = provider_service.model_options_payload()
        default_selection = options.get("defaultSelection")
        if not isinstance(default_selection, dict):
            raise ValueError(
                "No default provider/model available. Configure and validate a provider first."
            )
        connection_id = str(default_selection.get("connectionId") or "")
        model_id = str(default_selection.get("modelId") or "")
        if connection_id == "" or model_id == "":
            raise ValueError("Default selection is invalid")
        bundle = repositories.providers.get_bundle_by_id(connection_id)
        if bundle is None:
            raise ValueError(f"Default provider connection not found: {connection_id}")
        secret_payload = secret_store.load_provider_secret(bundle.connection.secret_ref)
        api_key = secret_payload.api_key if secret_payload is not None else None
        config = RuntimeWorkerConfig(
            model_id=model_id,
            provider=bundle.connection.runtime_provider,
            base_url=bundle.connection.base_url,
            api_key=api_key,
            model=model_id,
            request_timeout_ms=args.request_timeout_ms,
            ns_bot_home=args.ns_bot_home,
            workspace_path_default=args.workspace_path,
            fd_executable=args.fd_executable or None,
            rg_executable=args.rg_executable or None,
            tool_os_type=args.tool_os_type or None,
            max_steps=args.max_steps,
        )
        return (
            config,
            {
                "mode": "default-selection",
                "connectionId": bundle.connection.id,
                "providerId": bundle.connection.catalog_provider_id
                or bundle.connection.custom_slug
                or bundle.connection.runtime_provider,
                "runtimeProvider": bundle.connection.runtime_provider,
                "modelId": model_id,
                "baseUrl": bundle.connection.base_url,
                "hasApiKey": bool(api_key),
                "healthStatus": bundle.connection.health_status,
            },
        )
    finally:
        database.close()


def _handle_run_command(args: argparse.Namespace) -> int:
    config, resolved = _resolve_run_target(args)
    if args.diagnose:
        _print_json(
            {
                "resolved": resolved,
                "runtime": {
                    "modelId": config.model_id,
                    "provider": config.provider,
                    "model": config.model,
                    "baseUrl": config.base_url,
                    "hasApiKey": bool(config.api_key),
                    "requestTimeoutMs": config.request_timeout_ms,
                    "maxSteps": config.max_steps,
                },
                "workspacePath": args.workspace_path,
                "sessionKey": args.session_key or None,
            }
        )
        return 0

    metadata = RunMetadata(
        workspace_path=args.workspace_path,
        session_key=args.session_key or None,
    )
    auth_context = {
        "uid": "cli-user",
        "tid": "cli-team",
        "exp_epoch": 0,
    }

    print("[*] Initializing CodeAgentRuntimeService", file=sys.stderr)
    print(f"[*] Workspace: {args.workspace_path}", file=sys.stderr)
    model_disp = config.model or config.model_id
    print(f"[*] Model: {model_disp} (Provider: {config.provider})", file=sys.stderr)
    print(f"[*] Base URL: {config.base_url}", file=sys.stderr)

    service = CodeAgentRuntimeService(config)
    print(f"\n[*] Processing user input: {args.user_input}", file=sys.stderr)
    print("-" * 50, file=sys.stderr)
    result = service.process(
        run_id=args.run_id,
        user_input=args.user_input,
        auth_context=auth_context,
        metadata=metadata,
    )

    print("\n" + "=" * 50, file=sys.stderr)
    print("FINAL ANSWER:", file=sys.stderr)
    if result and "final_answer" in result:
        print(result["final_answer"])
    else:
        print("No final answer returned.", file=sys.stderr)

    if args.dump_result:
        print("\nRAW RESULT:", file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    effective_argv = _ensure_run_compat(
        list(argv) if argv is not None else sys.argv[1:]
    )
    args = parser.parse_args(effective_argv)

    try:
        if args.command == "providers":
            return _handle_providers_command(args)
        if args.command == "models":
            return _handle_models_command(args)
        if args.command == "run":
            return _handle_run_command(args)
        if args.command == "help":
            return _print_help_for_topic(parser, list(args.topic or []))
        raise ValueError(f"Unknown command: {args.command}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n[!] Error during execution: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
