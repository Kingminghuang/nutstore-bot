from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, cast
import uuid

import click
from fastapi import HTTPException
import typer

from nsbot_sidecar.api.acp_app import AcpAppConfig
from nsbot_sidecar.infrastructure.attachment_store import AttachmentStore
from nsbot_sidecar.infrastructure.local_paths import nsbot_home
from nsbot_sidecar.application.provider_service import ProviderService
from nsbot_sidecar.infrastructure.repositories import create_repositories
from nsbot_sidecar.runtime.engine import create_runtime_engine
from nsbot_sidecar.runtime.runtime_service import (
    RunMetadata,
    RuntimeWorkerConfig,
)
from nsbot_sidecar.application.session_service import SessionService
from nsbot_sidecar.infrastructure.secret_store import LocalSecretStore
from nsbot_sidecar.infrastructure.storage import connect_database
from nsbot_sidecar.application.timeline_service import TimelineService
from nsbot_sidecar.runtime.workspace_sidecar_indexer import WorkspaceSidecarIndexer


def _normalize_provider_ref(bundle: dict[str, Any]) -> str:
    return str(
        bundle.get("catalogProviderId")
        or bundle.get("customSlug")
        or bundle.get("runtimeProvider")
        or ""
    )


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _runtime_paths() -> dict[str, Path]:
    sidecar_root = Path(__file__).resolve().parent
    repo_root = sidecar_root.parent
    return {
        "repo_root": repo_root,
        "sidecar_root": sidecar_root,
        "templates_dir": repo_root / "templates",
        "search_tools_cache_root": sidecar_root / "vendor" / "search-tools",
        "prepare_search_tools_script": sidecar_root
        / "scripts"
        / "prepare_search_tools.py",
    }


def _resolve_target_triple() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        if machine in {"arm64", "aarch64"}:
            return "aarch64-apple-darwin"
        if machine in {"x86_64", "amd64"}:
            return "x86_64-apple-darwin"
    if sys.platform.startswith("win") and machine in {"x86_64", "amd64"}:
        return "x86_64-pc-windows-msvc"
    raise ValueError(
        f"Unsupported platform/arch for vendored search tools: {sys.platform}/{machine}"
    )


def _resolve_binary_name(tool_name: str) -> str:
    return f"{tool_name}.exe" if sys.platform.startswith("win") else tool_name


def _run_prepare_search_tools(
    *,
    sidecar_root: Path,
    target: str,
    prepare_search_tools_script: Path,
) -> None:
    if not prepare_search_tools_script.exists():
        raise RuntimeError(f"Missing prepare script: {prepare_search_tools_script}")
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(prepare_search_tools_script.relative_to(sidecar_root)),
            "--target",
            target,
        ],
        cwd=str(sidecar_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    detail = stderr or stdout or "Failed to prepare search tools"
    raise RuntimeError(detail)


def _handle_init_command(args: SimpleNamespace) -> int:
    ns_bot_home = Path(args.ns_bot_home).expanduser().resolve()
    ns_bot_home_existed = ns_bot_home.exists()
    ns_bot_home.mkdir(parents=True, exist_ok=True)
    bin_dir = ns_bot_home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    runtime_paths = _runtime_paths()
    templates_source = runtime_paths["templates_dir"]
    templates_target = ns_bot_home / "templates"
    templates_copied = False
    if templates_source.exists() and not templates_target.exists():
        shutil.copytree(templates_source, templates_target)
        templates_copied = True

    target = _resolve_target_triple()
    fd_binary_name = _resolve_binary_name("fd")
    rg_binary_name = _resolve_binary_name("rg")
    fd_target = bin_dir / fd_binary_name
    rg_target = bin_dir / rg_binary_name
    fd_cache = runtime_paths["search_tools_cache_root"] / target / "fd" / fd_binary_name
    rg_cache = runtime_paths["search_tools_cache_root"] / target / "rg" / rg_binary_name

    search_tools_downloaded = False
    search_tools_copied = False
    need_fd = not fd_target.exists()
    need_rg = not rg_target.exists()
    if need_fd or need_rg:
        if not fd_cache.exists() or not rg_cache.exists():
            _run_prepare_search_tools(
                sidecar_root=runtime_paths["sidecar_root"],
                target=target,
                prepare_search_tools_script=runtime_paths[
                    "prepare_search_tools_script"
                ],
            )
            search_tools_downloaded = True

        if need_fd:
            if not fd_cache.exists():
                raise RuntimeError(f"Missing vendored fd binary: {fd_cache}")
            shutil.copy2(fd_cache, fd_target)
            search_tools_copied = True
        if need_rg:
            if not rg_cache.exists():
                raise RuntimeError(f"Missing vendored rg binary: {rg_cache}")
            shutil.copy2(rg_cache, rg_target)
            search_tools_copied = True

    if not sys.platform.startswith("win"):
        os.chmod(fd_target, 0o755)
        os.chmod(rg_target, 0o755)

    _print_json(
        {
            "ok": True,
            "nsBotHome": str(ns_bot_home),
            "templatesPath": str(templates_target),
            "fdExecutable": str(fd_target),
            "rgExecutable": str(rg_target),
            "prepared": {
                "nsBotHomeCreated": not ns_bot_home_existed,
                "templatesCopied": templates_copied,
                "searchToolsCopied": search_tools_copied,
                "searchToolsDownloaded": search_tools_downloaded,
            },
        }
    )
    return 0


def _build_services(ns_bot_home_value: str):
    database = connect_database(ns_bot_home_value)
    repositories = create_repositories(cast(Any, database))
    secret_store = LocalSecretStore(ns_bot_home_value)
    provider_service = ProviderService(
        repositories=repositories.providers,
        secret_store=secret_store,
    )
    return database, repositories, secret_store, provider_service


def _build_session_service(ns_bot_home_value: str):
    database = connect_database(ns_bot_home_value)
    repositories = create_repositories(cast(Any, database))
    session_service = SessionService(
        workspaces=repositories.workspaces,
        sessions=repositories.sessions,
        attachments=repositories.attachments,
        draft_attachments=repositories.draft_attachments,
        attachment_store=AttachmentStore(ns_bot_home_value),
        timeline_service=TimelineService(
            sessions=repositories.sessions,
            acp_event_log=repositories.acp_event_log,
        ),
        workspace_sidecar_indexer=WorkspaceSidecarIndexer(),
    )
    return database, repositories, session_service


def _http_detail(exc: HTTPException) -> str:
    if isinstance(exc.detail, dict):
        return json.dumps(exc.detail, ensure_ascii=False)
    return str(exc.detail)


def _find_target_group(
    model_options: dict[str, Any], *, provider_ref: str
) -> dict[str, Any] | None:
    groups = model_options.get("groups")
    if not isinstance(groups, list):
        return None
    for group in groups:
        provider_id = str(group.get("providerId") or "")
        if provider_ref == provider_id:
            return group
    return None


def _first_model_id(group: dict[str, Any]) -> str | None:
    models = group.get("models")
    if not isinstance(models, list) or not models:
        return None
    model_id = str(models[0].get("modelId") or "").strip()
    return model_id or None


def _build_runtime_worker_config(
    *,
    args: SimpleNamespace,
    model_id: str,
    provider: str | None,
    base_url: str | None,
    api_key: str | None,
) -> RuntimeWorkerConfig:
    return RuntimeWorkerConfig(
        model_id=str(model_id),
        provider=(str(provider).strip() or None) if provider is not None else None,
        base_url=(str(base_url).strip() or None) if base_url is not None else None,
        api_key=(str(api_key).strip() or None) if api_key is not None else None,
        model=str(model_id),
        request_timeout_ms=args.request_timeout_ms,
        ns_bot_home=args.ns_bot_home,
        workspace_path_default=args.workspace_path,
        fd_executable=args.fd_executable or None,
        rg_executable=args.rg_executable or None,
        tool_os_type=args.tool_os_type or None,
        max_steps=args.max_steps,
    )


def _build_runtime_target_resolution(
    *,
    mode: str,
    config: RuntimeWorkerConfig,
    provider_id: str | None,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "providerId": provider_id,
        "runtimeProvider": str(config.provider or ""),
        "modelId": str(config.model_id),
        "baseUrl": config.base_url,
        "hasApiKey": bool(config.api_key),
    }


def _resolve_catalog_model_ids(bundle) -> list[str]:
    from nsbot_sidecar.providers.provider_catalog import list_providers

    provider_id = bundle.provider.catalog_provider_id or ""
    for provider in list_providers():
        if str(provider.get("id") or "") != provider_id:
            continue
        models = provider.get("models")
        if not isinstance(models, list):
            return []
        return [str(model.get("id") or "") for model in models if model.get("id")]
    return []


def _handle_providers_command(args: SimpleNamespace) -> int:
    database, repositories, _secret_store, provider_service = _build_services(
        args.ns_bot_home
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

        if args.providers_command == "use":
            target_ref = str(args.provider or "").strip()
            if target_ref == "":
                raise ValueError("Provider id is required")

            model_options = provider_service.model_options_payload()
            group = _find_target_group(model_options, provider_ref=target_ref)
            if group is None:
                raise ValueError(
                    f"No available connected provider found for '{target_ref}'"
                )

            provider_id = str(group.get("providerId") or "")
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
                    f"Model '{model_id}' is not available for provider '{provider_id}'"
                )

            updated = provider_service.update_provider(
                provider_id, {"preferredModelId": model_id}
            )
            _print_json(
                {
                    "ok": True,
                    "providerId": _normalize_provider_ref(updated),
                    "modelId": model_id,
                }
            )
            return 0

        raise ValueError(f"Unknown providers command: {args.providers_command}")
    finally:
        database.close()


def _handle_models_command(args: SimpleNamespace) -> int:
    database, repositories, _secret_store, provider_service = _build_services(
        args.ns_bot_home
    )
    try:
        if args.models_command == "list":
            payload = provider_service.model_options_payload()
            groups = payload.get("groups")
            filtered = []
            provider_filter = str(args.provider_id or args.provider or "").strip()
            for group in groups if isinstance(groups, list) else []:
                provider_id = str(group.get("providerId") or "")
                if provider_filter and provider_filter != provider_id:
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
                selected_provider = str(default_selection.get("providerId") or "")
                for group in groups if isinstance(groups, list) else []:
                    if str(group.get("providerId") or "") == selected_provider:
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

        provider_id = str(args.provider_id or "").strip()
        model_id = str(args.model or "").strip()
        bundle = repositories.providers.get_bundle_by_id(provider_id)
        if bundle is None:
            raise ValueError(f"Provider not found: {provider_id}")

        if args.models_command == "remove":
            if bundle.provider.kind != "custom":
                raise ValueError("models remove is only supported for custom providers")
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
                raise ValueError(f"Model '{model_id}' not found in provider '{provider_id}'")

            next_preferred = bundle.provider.preferred_model_id
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
                provider_id,
                {
                    "customModels": custom_models,
                    "preferredModelId": next_preferred,
                },
            )
            _print_json(
                {
                    "ok": True,
                    "providerId": provider_id,
                    "modelId": model_id,
                    "action": "removed",
                }
            )
            return 0

        if bundle.provider.kind == "custom":
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
                raise ValueError(f"Model '{model_id}' not found in provider '{provider_id}'")

            provider_service.update_provider(
                provider_id,
                {
                    "customModels": custom_models,
                    "preferredModelId": bundle.provider.preferred_model_id,
                },
            )
            _print_json(
                {
                    "ok": True,
                    "providerId": provider_id,
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
                f"Model '{model_id}' is not a catalog model for provider '{provider_id}'"
            )

        if (
            args.models_command == "enable"
            and bundle.provider.model_policy != "restricted"
        ):
            _print_json(
                {
                    "ok": True,
                    "providerId": provider_id,
                    "modelId": model_id,
                    "action": "enabled",
                    "modelPolicy": bundle.provider.model_policy,
                }
            )
            return 0

        if bundle.provider.model_policy == "restricted":
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
            provider_id,
            {
                "modelPolicy": "restricted",
                "enabledModelIds": enabled_in_order,
                "preferredModelId": bundle.provider.preferred_model_id,
            },
        )
        _print_json(
            {
                "ok": True,
                "providerId": provider_id,
                "modelId": model_id,
                "action": "enabled" if args.models_command == "enable" else "disabled",
            }
        )
        return 0
    finally:
        database.close()


def _handle_workspaces_command(args: SimpleNamespace) -> int:
    database, _repositories, session_service = _build_session_service(args.ns_bot_home)
    try:
        if args.workspaces_command == "list":
            _print_json(session_service.list_workspaces_payload())
            return 0

        if args.workspaces_command == "create":
            payload = {
                "name": str(args.name or "").strip(),
                "realPath": str(args.real_path or "").strip(),
                "pathLabel": str(args.path_label or "").strip()
                or str(args.real_path or "").strip(),
            }
            try:
                created = session_service.create_workspace(payload)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(created)
            return 0

        if args.workspaces_command == "update":
            payload: dict[str, Any] = {}
            name = str(args.name or "").strip()
            real_path = str(args.real_path or "").strip()
            path_label = str(args.path_label or "").strip()
            if name:
                payload["name"] = name
            if real_path:
                payload["realPath"] = real_path
            if path_label:
                payload["pathLabel"] = path_label
            if not payload:
                raise ValueError(
                    "At least one field is required: --name/--real-path/--path-label"
                )
            try:
                updated = session_service.update_workspace(args.workspace_id, payload)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(updated)
            return 0

        if args.workspaces_command == "delete":
            try:
                session_service.delete_workspace(args.workspace_id)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(
                {"ok": True, "workspaceId": args.workspace_id, "action": "deleted"}
            )
            return 0

        if args.workspaces_command == "sidecar-index-status":
            try:
                payload = session_service.workspace_sidecar_index_status_payload(
                    args.workspace_id
                )
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(payload)
            return 0

        raise ValueError(f"Unknown workspaces command: {args.workspaces_command}")
    finally:
        database.close()


def _handle_sessions_command(args: SimpleNamespace) -> int:
    database, _repositories, session_service = _build_session_service(args.ns_bot_home)
    try:
        if args.sessions_command == "list":
            try:
                payload = session_service.list_sessions_payload(args.workspace_id)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(payload)
            return 0

        if args.sessions_command == "create":
            payload: dict[str, Any] = {}
            provider_id = str(args.provider_id or "").strip()
            model_id = str(args.model_id or "").strip()
            if provider_id:
                payload["providerId"] = provider_id
            if model_id:
                payload["modelId"] = model_id
            try:
                created = session_service.create_session(args.workspace_id, payload)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(created)
            return 0

        if args.sessions_command == "update":
            payload = {"title": str(args.title or "").strip()}
            try:
                updated = session_service.update_session(args.session_id, payload)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(updated)
            return 0

        if args.sessions_command == "delete":
            try:
                session_service.delete_session(args.session_id)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json({"ok": True, "sessionId": args.session_id, "action": "deleted"})
            return 0

        if args.sessions_command == "timeline":
            try:
                payload = session_service.list_timeline_payload(
                    args.session_id,
                    limit=args.limit,
                    before_sequence=args.before_sequence,
                )
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(payload)
            return 0

        raise ValueError(f"Unknown sessions command: {args.sessions_command}")
    finally:
        database.close()


def _resolve_run_metadata(
    args: SimpleNamespace,
) -> tuple[RunMetadata, dict[str, Any] | None]:
    explicit_session_id = str(args.session_id or "").strip()
    if explicit_session_id == "":
        return (
            RunMetadata(
                workspace_path=args.workspace_path,
                session_key=args.session_key or None,
            ),
            None,
        )

    database, repositories, _secret_store, _provider_service = _build_services(
        args.ns_bot_home
    )
    try:
        try:
            session = repositories.sessions.get_by_id(explicit_session_id)
        except ValueError as exc:
            raise ValueError(f"Session not found: {explicit_session_id}") from exc
        try:
            workspace = repositories.workspaces.get_by_id(session.workspace_id)
        except ValueError as exc:
            raise ValueError(
                f"Workspace not found for session '{explicit_session_id}'"
            ) from exc
    finally:
        database.close()

    resolved_session_key = str(session.session_key or "").strip()
    if resolved_session_key == "":
        raise ValueError(f"Session '{explicit_session_id}' has empty session_key")

    return (
        RunMetadata(
            workspace_path=workspace.real_path,
            session_key=resolved_session_key,
        ),
        {
            "sessionId": session.id,
            "sessionKey": resolved_session_key,
            "workspaceId": workspace.id,
            "workspacePath": workspace.real_path,
            "activeProviderId": session.active_provider_id,
            "activeModelId": session.active_model_id,
        },
    )


def _resolve_run_target(
    args: SimpleNamespace,
) -> tuple[RuntimeWorkerConfig, dict[str, Any]]:
    database, repositories, secret_store, provider_service = _build_services(
        args.ns_bot_home
    )
    try:
        explicit_provider_id = str(args.provider_id or "").strip()
        explicit_selected_model = str(args.selected_model_id or "").strip()

        if explicit_provider_id:
            bundle = repositories.providers.get_bundle_by_id(explicit_provider_id)
            if bundle is None:
                raise ValueError(f"Provider not found: {explicit_provider_id}")
            selected_model = (
                explicit_selected_model or bundle.provider.preferred_model_id
            )
            if not selected_model:
                options = provider_service.model_options_payload()
                group = _find_target_group(options, provider_ref=explicit_provider_id)
                if group is None:
                    raise ValueError(f"No available models for provider '{explicit_provider_id}'")
                selected_model = _first_model_id(group)
                if selected_model is None:
                    raise ValueError(f"No available models for provider '{explicit_provider_id}'")

            secret_payload = secret_store.load_provider_secret(
                bundle.provider.secret_ref
            )
            api_key = secret_payload.api_key if secret_payload is not None else None
            config = _build_runtime_worker_config(
                args=args,
                model_id=str(selected_model),
                provider=bundle.provider.runtime_provider,
                base_url=bundle.provider.base_url,
                api_key=api_key,
            )
            return (
                config,
                _build_runtime_target_resolution(
                    mode="provider",
                    config=config,
                    provider_id=bundle.provider.id,
                )
                | {
                    "providerId": bundle.provider.catalog_provider_id
                    or bundle.provider.custom_slug
                    or bundle.provider.runtime_provider,
                    "runtimeProvider": bundle.provider.runtime_provider,
                },
            )

        options = provider_service.model_options_payload()
        default_selection = options.get("defaultSelection")
        if not isinstance(default_selection, dict):
            raise ValueError(
                "No default provider/model available. Configure a provider first."
            )
        provider_id = str(default_selection.get("providerId") or "")
        model_id = str(default_selection.get("modelId") or "")
        if provider_id == "" or model_id == "":
            raise ValueError("Default selection is invalid")
        bundle = repositories.providers.get_bundle_by_id(provider_id)
        if bundle is None:
            raise ValueError(f"Default provider not found: {provider_id}")
        secret_payload = secret_store.load_provider_secret(bundle.provider.secret_ref)
        api_key = secret_payload.api_key if secret_payload is not None else None
        config = _build_runtime_worker_config(
            args=args,
            model_id=model_id,
            provider=bundle.provider.runtime_provider,
            base_url=bundle.provider.base_url,
            api_key=api_key,
        )
        return (
            config,
            _build_runtime_target_resolution(
                mode="default-provider",
                config=config,
                provider_id=bundle.provider.id,
            )
            | {
                "providerId": bundle.provider.catalog_provider_id
                or bundle.provider.custom_slug
                or bundle.provider.runtime_provider,
                "runtimeProvider": bundle.provider.runtime_provider,
            },
        )
    finally:
        database.close()


def _handle_run_command(args: SimpleNamespace) -> int:
    config, resolved = _resolve_run_target(args)
    metadata, resolved_session = _resolve_run_metadata(args)
    effective_workspace_path = metadata.workspace_path or args.workspace_path
    if args.diagnose:
        _print_json(
            {
                "resolved": resolved,
                "resolvedSession": resolved_session,
                "runtime": {
                    "modelId": config.model_id,
                    "provider": config.provider,
                    "model": config.model,
                    "baseUrl": config.base_url,
                    "hasApiKey": bool(config.api_key),
                    "requestTimeoutMs": config.request_timeout_ms,
                    "maxSteps": config.max_steps,
                    "fdExecutable": config.fd_executable,
                    "rgExecutable": config.rg_executable,
                },
                "workspacePath": effective_workspace_path,
                "sessionKey": metadata.session_key,
            }
        )
        return 0

    auth_context = {
        "uid": "cli-user",
        "tid": "cli-team",
        "exp_epoch": 0,
    }

    print("[*] Initializing RuntimeEngine", file=sys.stderr)
    print(f"[*] Workspace: {effective_workspace_path}", file=sys.stderr)
    model_disp = config.model or config.model_id
    print(f"[*] Model: {model_disp} (Provider: {config.provider})", file=sys.stderr)
    print(f"[*] Base URL: {config.base_url}", file=sys.stderr)

    runtime_engine = create_runtime_engine(config)
    print(f"\n[*] Processing user input: {args.user_input}", file=sys.stderr)
    print("-" * 50, file=sys.stderr)
    result = runtime_engine.process(
        turn_id=args.turn_id,
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


def _ns_bot_home_from_ctx(ctx: typer.Context) -> str:
    return str(ctx.obj.get("ns_bot_home") if isinstance(ctx.obj, dict) else nsbot_home())


def _build_acp_app_config(ns_bot_home_value: str) -> AcpAppConfig:
    return AcpAppConfig(
        ns_bot_home=ns_bot_home_value,
        fd_executable=os.environ.get("NSBOT_FD_EXECUTABLE") or None,
        rg_executable=os.environ.get("NSBOT_RG_EXECUTABLE") or None,
    )


def _run_acp_mode(ns_bot_home_value: str) -> int:
    from nsbot_sidecar.api import acp_stdio

    return acp_stdio.main(config=_build_acp_app_config(ns_bot_home_value))


def _parse_root_mode_arguments(argv: list[str]) -> tuple[bool, str | None, bool, bool]:
    acp = False
    ns_bot_home_value: str | None = None
    help_requested = False
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"--help", "-h"}:
            help_requested = True
            index += 1
            continue
        if token == "--acp":
            acp = True
            index += 1
            continue
        if token.startswith("--ns-bot-home="):
            ns_bot_home_value = token.split("=", 1)[1]
            index += 1
            continue
        if token == "--ns-bot-home":
            if index + 1 >= len(argv):
                raise click.UsageError("Option '--ns-bot-home' requires an argument.")
            ns_bot_home_value = argv[index + 1]
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return acp, ns_bot_home_value, help_requested, True
    return acp, ns_bot_home_value, help_requested, False


def _run_with_error_handling(fn) -> int:
    try:
        code = int(fn())
        if code != 0:
            raise RuntimeError(f"Command failed with exit code {code}")
        return code
    except Exception:
        raise


HELP_OPTION_NAMES = {"help_option_names": ["-h", "--help"]}


app = typer.Typer(
    help="NSBot CLI for provider/model management and runtime execution",
    context_settings=HELP_OPTION_NAMES,
)
providers_app = typer.Typer(
    help="Manage providers",
    context_settings=HELP_OPTION_NAMES,
)
models_app = typer.Typer(
    help="Manage model options",
    context_settings=HELP_OPTION_NAMES,
)
workspaces_app = typer.Typer(
    help="Manage workspaces",
    context_settings=HELP_OPTION_NAMES,
)
sessions_app = typer.Typer(
    help="Manage sessions",
    context_settings=HELP_OPTION_NAMES,
)
agent_app = typer.Typer(
    help="Agent runtime commands",
    context_settings=HELP_OPTION_NAMES,
)


@app.callback()
def root(
    ctx: typer.Context,
    ns_bot_home_value: str = typer.Option(
        str(nsbot_home()),
        "--ns-bot-home",
        help="Path to NSBot data directory.",
    ),
    acp_mode: bool = typer.Option(
        False,
        "--acp",
        help="Start the sidecar in ACP stdio mode.",
    ),
) -> None:
    ctx.obj = {"ns_bot_home": ns_bot_home_value, "acp": acp_mode}


@providers_app.command("list")
def providers_list(ctx: typer.Context) -> None:
    _run_with_error_handling(
        lambda: _handle_providers_command(
            SimpleNamespace(ns_bot_home=_ns_bot_home_from_ctx(ctx), providers_command="list")
        )
    )


@providers_app.command("use")
def providers_use(
    ctx: typer.Context,
    provider: str = typer.Argument(..., help="Provider id or connection id"),
    model: str = typer.Option("", "--model", help="Preferred model id"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_providers_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                providers_command="use",
                provider=provider,
                model=model,
            )
        )
    )


@providers_app.command("delete")
def providers_delete(
    ctx: typer.Context,
    provider_id: str = typer.Option(..., "--provider-id", help="Provider id"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_providers_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                providers_command="delete",
                provider_id=provider_id,
            )
        )
    )


@models_app.command("list")
def models_list(
    ctx: typer.Context,
    provider_id: str = typer.Option("", "--provider-id", help="Filter by provider id"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_models_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                models_command="list",
                provider_id=provider_id,
            )
        )
    )


@models_app.command("status")
def models_status(ctx: typer.Context) -> None:
    _run_with_error_handling(
        lambda: _handle_models_command(
            SimpleNamespace(ns_bot_home=_ns_bot_home_from_ctx(ctx), models_command="status")
        )
    )


@models_app.command("enable")
def models_enable(
    ctx: typer.Context,
    provider_id: str = typer.Option(..., "--provider-id"),
    model: str = typer.Option(..., "--model"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_models_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                models_command="enable",
                provider_id=provider_id,
                model=model,
            )
        )
    )


@models_app.command("disable")
def models_disable(
    ctx: typer.Context,
    provider_id: str = typer.Option(..., "--provider-id"),
    model: str = typer.Option(..., "--model"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_models_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                models_command="disable",
                provider_id=provider_id,
                model=model,
            )
        )
    )


@models_app.command("remove")
def models_remove(
    ctx: typer.Context,
    provider_id: str = typer.Option(..., "--provider-id"),
    model: str = typer.Option(..., "--model"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_models_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                models_command="remove",
                provider_id=provider_id,
                model=model,
            )
        )
    )


@workspaces_app.command("list")
def workspaces_list(ctx: typer.Context) -> None:
    _run_with_error_handling(
        lambda: _handle_workspaces_command(
            SimpleNamespace(ns_bot_home=_ns_bot_home_from_ctx(ctx), workspaces_command="list")
        )
    )


@workspaces_app.command("create")
def workspaces_create(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name"),
    real_path: str = typer.Option(..., "--real-path"),
    path_label: str = typer.Option("", "--path-label"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_workspaces_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                workspaces_command="create",
                name=name,
                real_path=real_path,
                path_label=path_label,
            )
        )
    )


@workspaces_app.command("update")
def workspaces_update(
    ctx: typer.Context,
    workspace_id: str = typer.Option(..., "--workspace-id"),
    name: str = typer.Option("", "--name"),
    real_path: str = typer.Option("", "--real-path"),
    path_label: str = typer.Option("", "--path-label"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_workspaces_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                workspaces_command="update",
                workspace_id=workspace_id,
                name=name,
                real_path=real_path,
                path_label=path_label,
            )
        )
    )


@workspaces_app.command("delete")
def workspaces_delete(
    ctx: typer.Context,
    workspace_id: str = typer.Option(..., "--workspace-id"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_workspaces_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                workspaces_command="delete",
                workspace_id=workspace_id,
            )
        )
    )


@workspaces_app.command("sidecar-index-status")
def workspaces_sidecar_index_status(
    ctx: typer.Context,
    workspace_id: str = typer.Option(..., "--workspace-id"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_workspaces_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                workspaces_command="sidecar-index-status",
                workspace_id=workspace_id,
            )
        )
    )


@sessions_app.command("list")
def sessions_list(
    ctx: typer.Context,
    workspace_id: str = typer.Option(..., "--workspace-id"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_sessions_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                sessions_command="list",
                workspace_id=workspace_id,
            )
        )
    )


@sessions_app.command("create")
def sessions_create(
    ctx: typer.Context,
    workspace_id: str = typer.Option(..., "--workspace-id"),
    provider_id: str = typer.Option("", "--provider-id"),
    model_id: str = typer.Option("", "--model-id"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_sessions_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                sessions_command="create",
                workspace_id=workspace_id,
                provider_id=provider_id,
                model_id=model_id,
            )
        )
    )


@sessions_app.command("update")
def sessions_update(
    ctx: typer.Context,
    session_id: str = typer.Option(..., "--session-id"),
    title: str = typer.Option(..., "--title"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_sessions_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                sessions_command="update",
                session_id=session_id,
                title=title,
            )
        )
    )


@sessions_app.command("delete")
def sessions_delete(
    ctx: typer.Context,
    session_id: str = typer.Option(..., "--session-id"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_sessions_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                sessions_command="delete",
                session_id=session_id,
            )
        )
    )


@sessions_app.command("timeline")
def sessions_timeline(
    ctx: typer.Context,
    session_id: str = typer.Option(..., "--session-id"),
    limit: int | None = typer.Option(None, "--limit"),
    before_sequence: int | None = typer.Option(None, "--before-sequence"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_sessions_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                sessions_command="timeline",
                session_id=session_id,
                limit=limit,
                before_sequence=before_sequence,
            )
        )
    )


@app.command("init")
def init_command(ctx: typer.Context) -> None:
    _run_with_error_handling(
        lambda: _handle_init_command(SimpleNamespace(ns_bot_home=_ns_bot_home_from_ctx(ctx)))
    )


@agent_app.command("run")
def agent_run_command(
    ctx: typer.Context,
    prompt: str = typer.Option(..., "--prompt", help="Task prompt"),
    turn_id: str = typer.Option(str(uuid.uuid4()), "--turn-id"),
    workspace_path: str = typer.Option(os.getcwd(), "--workspace-path"),
    provider_id: str = typer.Option("", "--provider-id"),
    selected_model_id: str = typer.Option("", "--selected-model-id"),
    request_timeout_ms: int = typer.Option(60000, "--request-timeout-ms"),
    max_steps: int = typer.Option(20, "--max-steps"),
    fd_executable: str = typer.Option("", "--fd-executable"),
    rg_executable: str = typer.Option("", "--rg-executable"),
    tool_os_type: str = typer.Option("", "--tool-os-type"),
    session_key: str = typer.Option("", "--session-key"),
    session_id: str = typer.Option("", "--session-id"),
    dump_result: bool = typer.Option(False, "--dump-result"),
    diagnose: bool = typer.Option(False, "--diagnose"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_run_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                user_input=prompt,
                turn_id=turn_id,
                workspace_path=workspace_path,
                provider_id=provider_id,
                selected_model_id=selected_model_id,
                request_timeout_ms=request_timeout_ms,
                max_steps=max_steps,
                fd_executable=fd_executable or os.getenv("NSBOT_FD_EXECUTABLE", "").strip(),
                rg_executable=rg_executable or os.getenv("NSBOT_RG_EXECUTABLE", "").strip(),
                tool_os_type=tool_os_type,
                session_key=session_key,
                session_id=session_id,
                dump_result=dump_result,
                diagnose=diagnose,
            )
        )
    )


app.add_typer(providers_app, name="providers")
app.add_typer(models_app, name="models")
app.add_typer(workspaces_app, name="workspaces")
app.add_typer(sessions_app, name="sessions")
app.add_typer(agent_app, name="agent")


def main(argv: list[str] | None = None) -> int:
    command = typer.main.get_command(app)
    effective_argv = list(argv) if argv is not None else sys.argv[1:]
    try:
        acp_mode, ns_bot_home_value, help_requested, has_command = _parse_root_mode_arguments(
            effective_argv
        )
        if acp_mode and not help_requested:
            if has_command:
                raise click.UsageError(
                    "ACP mode cannot be combined with subcommands. Use 'nsbot-sidecar --acp'."
                )
            resolved_ns_bot_home = ns_bot_home_value or str(nsbot_home())
            return _run_acp_mode(resolved_ns_bot_home)
        command.main(args=effective_argv, prog_name="nsbot-sidecar", standalone_mode=False)
        return 0
    except click.ClickException as exc:
        exc.show(file=sys.stderr)
        return int(exc.exit_code)
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception as exc:  # noqa: BLE001
        print(f"\n[!] Error during execution: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
