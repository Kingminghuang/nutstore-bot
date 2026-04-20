from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, cast
import uuid

import anyio
import click
from fastapi import HTTPException
import typer

from nsbot_sidecar.api.acp_app import AcpAppConfig
from nsbot_sidecar.infrastructure.local_paths import nsbot_home
from nsbot_sidecar.application.provider_service import ProviderService
from nsbot_sidecar.infrastructure.repositories import create_repositories
from nsbot_sidecar.runtime.engine import create_runtime_engine
from nsbot_sidecar.runtime.types import RunMetadata, RuntimeWorkerConfig
from nsbot_sidecar.application.session_service import SessionService
from nsbot_sidecar.infrastructure.secret_store import LocalSecretStore
from nsbot_sidecar.infrastructure.storage import connect_database
from nsbot_sidecar.application.timeline_service import TimelineService
from nsbot_sidecar.runtime.workspace_sidecar_indexer import WorkspaceSidecarIndexer


class _CliTurnExecutionError(RuntimeError):
    def __init__(self, message: str, *, runtime_events: list[dict[str, Any]]):
        super().__init__(message)
        self.runtime_events = runtime_events


def _normalize_provider_ref(bundle: dict[str, Any]) -> str:
    return str(
        bundle.get("catalogProviderId")
        or bundle.get("customSlug")
        or bundle.get("runtimeProvider")
        or ""
    )


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


_TODO_LINE_PATTERN = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+?)\s*$")
_TIMELINE_DEPRECATION_NOTICE = (
    "Deprecated: timeline contains ACP-derived compatibility rows. "
    "Use events for the Codex SDK-like event stream."
)


TEMPLATE_REQUIRED_FILES = (
    "IDENTITFY.md",
    "SOUL.md",
    "USER.md",
    "TOOLS.md",
)


def _templates_complete(templates_dir: Path) -> bool:
    return all((templates_dir / filename).exists() for filename in TEMPLATE_REQUIRED_FILES)


def _iter_template_source_candidates() -> list[Path]:
    candidates: list[Path] = []

    env_source = str(os.environ.get("NSBOT_TEMPLATES_SOURCE") or "").strip()
    if env_source:
        candidates.append(Path(env_source).expanduser().resolve())

    seeds: list[Path] = []
    for raw in (Path(__file__), Path(sys.argv[0]), Path(sys.executable)):
        try:
            seeds.append(raw.expanduser().resolve())
        except Exception:
            continue

    seen: set[Path] = set()
    for seed in seeds:
        for parent in [seed.parent, *seed.parents]:
            for candidate in (parent / "templates", parent / "runtime" / "templates"):
                if candidate in seen:
                    continue
                seen.add(candidate)
                candidates.append(candidate)

    return candidates


def _resolve_template_source() -> Path | None:
    for candidate in _iter_template_source_candidates():
        if not candidate.exists() or not candidate.is_dir():
            continue
        if _templates_complete(candidate):
            return candidate
    return None


def _copy_missing_tree_entries(source: Path, destination: Path) -> None:
    for entry in source.rglob("*"):
        relative = entry.relative_to(source)
        target = destination / relative
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, target)


def _ensure_templates_in_ns_bot_home(ns_bot_home_value: str) -> None:
    home = Path(ns_bot_home_value).expanduser().resolve()
    templates_target = home / "templates"
    if _templates_complete(templates_target):
        return

    source = _resolve_template_source()
    if source is None:
        return

    home.mkdir(parents=True, exist_ok=True)
    if not templates_target.exists():
        shutil.copytree(source, templates_target)
        return

    _copy_missing_tree_entries(source, templates_target)


def _build_services(ns_bot_home_value: str, db_path: str | None = None):
    database = connect_database(ns_bot_home_value, db_path=db_path)
    repositories = create_repositories(cast(Any, database))
    secret_store = LocalSecretStore(ns_bot_home_value)
    provider_service = ProviderService(
        repositories=repositories.providers,
        default_model_selection=repositories.default_model_selection,
        secret_store=secret_store,
    )
    return database, repositories, secret_store, provider_service


def _build_session_service(ns_bot_home_value: str, db_path: str | None = None):
    database = connect_database(ns_bot_home_value, db_path=db_path)
    repositories = create_repositories(cast(Any, database))
    session_service = SessionService(
        workspaces=repositories.workspaces,
        sessions=repositories.sessions,
        ns_bot_home=ns_bot_home_value,
        timeline_service=TimelineService(
            sessions=repositories.sessions,
            acp_event_log=repositories.acp_event_log,
        ),
        workspace_sidecar_indexer=WorkspaceSidecarIndexer(),
    )
    return database, repositories, session_service


def _parse_model_identity(identity: str) -> tuple[str, str] | None:
    token = str(identity or "").strip()
    if token == "":
        return None
    if ":" not in token:
        return None
    left, right = token.split(":", 1)
    provider_id = left.strip()
    model_id = right.strip()
    if provider_id == "" or model_id == "":
        return None
    return provider_id, model_id


def _find_provider_and_model_from_identity(
    model_options: dict[str, Any], *, identity: str
) -> tuple[str, str]:
    parsed = _parse_model_identity(identity)
    if parsed is not None:
        return parsed

    groups = model_options.get("groups")
    if not isinstance(groups, list):
        raise ValueError("No model groups available")

    matches: list[tuple[str, str]] = []
    for group in groups:
        provider_id = str(group.get("providerId") or "").strip()
        models = group.get("models")
        if provider_id == "" or not isinstance(models, list):
            continue
        for model in models:
            model_id = str(model.get("modelId") or "").strip()
            if model_id == str(identity).strip():
                matches.append((provider_id, model_id))

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            "Model identity is ambiguous. Use '<providerId>:<modelId>' format."
        )
    raise ValueError(f"Model not found: {identity}")


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


def _find_provider_id_by_model_id(
    model_options: dict[str, Any], *, model_id: str
) -> str:
    groups = model_options.get("groups")
    if not isinstance(groups, list):
        raise ValueError("No model groups available")

    matches: list[str] = []
    for group in groups:
        provider_id = str(group.get("providerId") or "").strip()
        models = group.get("models")
        if provider_id == "" or not isinstance(models, list):
            continue
        for model in models:
            if str(model.get("modelId") or "").strip() == model_id:
                matches.append(provider_id)
                break

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            "Model id is ambiguous. Use --provider-id to disambiguate the target provider."
        )
    raise ValueError(
        f"Model '{model_id}' was not found in available providers. Use --provider-id to specify the target provider."
    )


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
        request_timeout_ms=int(getattr(args, "request_timeout_ms", 60000)),
        ns_bot_home=args.ns_bot_home,
        workspace_path_default=args.workspace,
        fd_executable=(
            str(os.getenv("NSBOT_FD_EXECUTABLE") or "").strip() or None
        ),
        rg_executable=(
            str(os.getenv("NSBOT_RG_EXECUTABLE") or "").strip() or None
        ),
        tool_os_type=(str(os.getenv("NSBOT_TOOL_OS_TYPE") or "").strip() or None),
        max_steps=int(getattr(args, "max_steps", 20)),
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


def _handle_models_command(args: SimpleNamespace) -> int:
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


def _handle_workspaces_command(args: SimpleNamespace) -> int:
    database, _repositories, session_service = _build_session_service(
        args.ns_bot_home,
        db_path=args.db_path,
    )
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


def _handle_threads_command(args: SimpleNamespace) -> int:
    if args.threads_command == "list":
        return _handle_threads_list_command(args)

    if args.threads_command == "get":
        return _handle_thread_get_command(args)

    if args.threads_command == "delete":
        return _handle_thread_delete_command(args)

    if args.threads_command == "update":
        database, _repositories, session_service = _build_session_service(
            args.ns_bot_home,
            db_path=args.db_path,
        )
        try:
            payload = {"title": str(args.title or "").strip()}
            try:
                updated = session_service.update_session(args.thread_id, payload)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(updated)
            return 0
        finally:
            database.close()

    raise ValueError(f"Unknown threads command: {args.threads_command}")


def _resolve_run_target(
    args: SimpleNamespace,
) -> tuple[RuntimeWorkerConfig, dict[str, Any], str, str]:
    database, repositories, secret_store, provider_service = _build_services(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        options = provider_service.model_options_payload()
        default_selection = options.get("defaultSelection")
        if not isinstance(default_selection, dict):
            raise ValueError(
                "No default provider/model available. Configure a provider first."
            )
        provider_id = str(default_selection.get("providerId") or "")
        selected_model = str(args.model or "").strip()
        model_id = selected_model or str(default_selection.get("modelId") or "")
        if provider_id == "" or model_id == "":
            raise ValueError("Default selection is invalid")

        group = _find_target_group(options, provider_ref=provider_id)
        allowed_ids = {
            str(model.get("modelId") or "")
            for model in (
                group.get("models")
                if isinstance(group, dict) and isinstance(group.get("models"), list)
                else []
            )
        }
        if selected_model and model_id not in allowed_ids:
            raise ValueError(
                f"Model '{model_id}' is not available for provider '{provider_id}'"
            )

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
            provider_id,
            model_id,
        )
    finally:
        database.close()


def _resolve_workspace_record(repositories, workspace_path: str):
    resolved = str(Path(workspace_path).expanduser().resolve())
    for workspace in repositories.workspaces.list():
        if str(Path(workspace.real_path).expanduser().resolve()) == resolved:
            return workspace
    name = Path(resolved).name or "workspace"
    return repositories.workspaces.create(name=name, path_label=resolved, real_path=resolved)


def _resolve_thread_context(
    args: SimpleNamespace,
    *,
    active_provider_id: str | None,
    active_model_id: str | None,
) -> tuple[str, RunMetadata, dict[str, Any]]:
    database, repositories, _secret_store, _provider_service = _build_services(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        explicit_thread_id = str(args.thread_id or "").strip()
        if explicit_thread_id:
            try:
                session = repositories.sessions.get_by_id(explicit_thread_id)
            except ValueError as exc:
                raise ValueError(f"Thread not found: {explicit_thread_id}") from exc
            workspace = repositories.workspaces.get_by_id(session.workspace_id)
            session = repositories.sessions.touch(
                session.id,
                active_provider_id=active_provider_id or session.active_provider_id,
                active_model_id=active_model_id or session.active_model_id,
            )
            thread_id = session.id
        else:
            workspace = _resolve_workspace_record(repositories, args.workspace)
            session = repositories.sessions.create(
                workspace_id=workspace.id,
                active_provider_id=active_provider_id,
                active_model_id=active_model_id,
            )
            thread_id = session.id

        metadata = RunMetadata(
            workspace_path=workspace.real_path,
            session_key=str(session.session_key or "").strip() or None,
        )
        payload = {
            "threadId": thread_id,
            "sessionKey": session.session_key,
            "workspaceId": workspace.id,
            "workspacePath": workspace.real_path,
            "activeProviderId": session.active_provider_id,
            "activeModelId": session.active_model_id,
        }
        return thread_id, metadata, payload
    finally:
        database.close()


def _thread_pid_file(ns_bot_home_value: str, thread_id: str) -> Path:
    runtime_dir = Path(ns_bot_home_value).expanduser().resolve() / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / f"thread-{thread_id}.pid"


def _run_pid_file(ns_bot_home_value: str, run_id: str) -> Path:
    runtime_dir = Path(ns_bot_home_value).expanduser().resolve() / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / f"run-{run_id}.pid"


def _run_record_file(ns_bot_home_value: str, run_id: str) -> Path:
    runtime_dir = Path(ns_bot_home_value).expanduser().resolve() / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / f"run-{run_id}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_run_record(ns_bot_home_value: str, run_id: str, payload: dict[str, Any]) -> None:
    path = _run_record_file(ns_bot_home_value, run_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_run_record(ns_bot_home_value: str, run_id: str) -> dict[str, Any]:
    path = _run_record_file(ns_bot_home_value, run_id)
    if not path.exists():
        raise ValueError(f"Run not found: {run_id}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Run metadata is corrupted: {run_id}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Run metadata is invalid: {run_id}")
    return payload


def _update_run_record(ns_bot_home_value: str, run_id: str, **updates: Any) -> dict[str, Any]:
    payload = _read_run_record(ns_bot_home_value, run_id)
    payload.update(updates)
    _write_run_record(ns_bot_home_value, run_id, payload)
    return payload


def _write_pid_file(path: Path, pid: int) -> None:
    path.write_text(str(pid), encoding="utf-8")


def _unlink_pid_file_if_matches(path: Path, pid: int) -> None:
    if not path.exists():
        return
    value = path.read_text(encoding="utf-8").strip()
    if value == str(pid):
        path.unlink(missing_ok=True)


def _derive_thread_status(*, session, pid_file: Path) -> str:
    if pid_file.exists():
        return "running"
    if int(getattr(session, "message_count", 0)) <= 0:
        return "pending"
    if str(getattr(session, "title_status", "")) == "failed":
        return "failed"
    return "succeeded"


def _execute_agent_turn(
    *,
    args: SimpleNamespace,
    run_id: str,
    thread_id: str,
    prompt: str,
    metadata: RunMetadata,
    resolved: dict[str, Any],
) -> dict[str, Any]:
    config, _resolved_target, _provider_id, _model_id = _resolve_run_target(args)

    auth_context = {
        "uid": "cli-user",
        "tid": "cli-team",
        "exp_epoch": 0,
    }
    runtime_engine = create_runtime_engine(config)
    runtime_events: list[dict[str, Any]] = []

    def _capture_runtime_event(event: dict[str, Any]) -> None:
        runtime_events.append(event)

    try:
        result = anyio.run(
            runtime_engine.process_async,
            run_id,
            prompt,
            auth_context,
            metadata,
            _capture_runtime_event,
        )
    except Exception as exc:
        raise _CliTurnExecutionError(str(exc), runtime_events=runtime_events) from exc

    output = {
        "runId": run_id,
        "threadId": thread_id,
        "workspace": metadata.workspace_path,
        "resolved": resolved,
        "runtimeEvents": runtime_events,
        "result": result,
        "finalAnswer": result.get("final_answer") if isinstance(result, dict) else None,
    }
    return output


def _parse_tool_arguments(tool_call: dict[str, Any]) -> Any:
    arguments_text = str(tool_call.get("argumentsText") or "").strip()
    if arguments_text == "":
        return None
    try:
        return json.loads(arguments_text)
    except json.JSONDecodeError:
        return arguments_text


def _tool_content_text(tool_result: dict[str, Any] | None) -> str:
    if not isinstance(tool_result, dict):
        return ""
    content = tool_result.get("content")
    if not isinstance(content, list):
        return ""
    blocks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip().lower() != "text":
            continue
        text = str(item.get("text") or "").strip()
        if text:
            blocks.append(text)
    return "\n".join(blocks)


def _tool_error_text(tool_result: dict[str, Any] | None, fallback: str = "") -> str:
    if isinstance(tool_result, dict):
        error = tool_result.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            if message:
                return message
        if tool_result.get("isError"):
            return _tool_content_text(tool_result) or fallback
    return fallback


def _tool_failed(tool_result: dict[str, Any] | None, fallback_error: str = "") -> bool:
    if fallback_error.strip():
        return True
    if not isinstance(tool_result, dict):
        return False
    if tool_result.get("isError"):
        return True
    error = tool_result.get("error")
    return isinstance(error, dict) and str(error.get("message") or "").strip() != ""


def _infer_todo_items(plan_text: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for line in plan_text.splitlines():
        match = _TODO_LINE_PATTERN.match(line)
        if match is None:
            continue
        text = match.group(1).strip()
        if text:
            matches.append({"text": text, "completed": False})
    if matches:
        return matches

    fallback = plan_text.strip()
    if fallback == "":
        return []
    return [{"text": fallback, "completed": False}]


def _command_text_for_tool(name: str, arguments: Any) -> str:
    if name == "python_exec_agent":
        if isinstance(arguments, dict):
            for key in ("code", "codeAction", "code_action"):
                value = arguments.get(key)
                if isinstance(value, str) and value.strip():
                    return f"python_exec_agent {value.strip()}"
        if isinstance(arguments, str) and arguments.strip():
            return f"python_exec_agent {arguments.strip()}"
        return "python_exec_agent"

    if name == "grep":
        if isinstance(arguments, dict):
            pattern = str(arguments.get("pattern") or "").strip()
            path = str(arguments.get("path") or "").strip()
            parts = ["rg"]
            if pattern:
                parts.append(pattern)
            if path:
                parts.append(path)
            return " ".join(parts)
        return "rg"

    if name == "find":
        if isinstance(arguments, dict):
            pattern = str(arguments.get("pattern") or "").strip()
            path = str(arguments.get("path") or "").strip()
            parts = ["fd"]
            if pattern:
                parts.append(pattern)
            if path:
                parts.append(path)
            return " ".join(parts)
        return "fd"

    return name


def _file_change_item_for_tool(tool_call_id: str, tool_name: str, arguments: Any) -> dict[str, Any]:
    path_value = None
    kind = "update"
    if isinstance(arguments, dict):
        raw_path = arguments.get("path")
        if isinstance(raw_path, str) and raw_path.strip():
            path_value = raw_path.strip()
    if tool_name == "edit":
        kind = "update"
    elif tool_name == "write":
        mutation_kind = None
        if isinstance(arguments, dict):
            mutation_kind = arguments.get("mutationKind")
        if isinstance(mutation_kind, str) and mutation_kind in {"add", "update"}:
            kind = mutation_kind
    changes = []
    if path_value is not None:
        changes.append(
            {
                "path": path_value,
                "kind": kind,
            }
        )
    return {
        "id": tool_call_id,
        "type": "file_change",
        "changes": changes,
        "status": "completed",
    }


def _mcp_tool_item_for_tool(
    tool_call_id: str,
    tool_name: str,
    arguments: Any,
    tool_result: dict[str, Any] | None,
    *,
    failed: bool,
    error_text: str,
) -> dict[str, Any]:
    item = {
        "id": tool_call_id,
        "type": "mcp_tool_call",
        "server": "workspace",
        "tool": tool_name,
        "arguments": arguments,
        "status": "failed" if failed else "completed",
    }
    if failed:
        item["error"] = {"message": error_text or "Tool call failed"}
        return item

    result_payload: dict[str, Any] = {
        "content": tool_result.get("content") if isinstance(tool_result, dict) else [],
        "structured_content": (
            tool_result.get("structuredContent") if isinstance(tool_result, dict) else None
        ),
    }
    item["result"] = result_payload
    return item


def _command_item_for_tool(
    tool_call_id: str,
    tool_name: str,
    arguments: Any,
    tool_result: dict[str, Any] | None,
    *,
    failed: bool,
    error_text: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    aggregated_output = _tool_content_text(tool_result)
    if failed and error_text:
        aggregated_output = error_text if aggregated_output == "" else f"{aggregated_output}\n{error_text}"

    started_item = {
        "id": tool_call_id,
        "type": "command_execution",
        "command": _command_text_for_tool(tool_name, arguments),
        "aggregated_output": "",
        "status": "in_progress",
    }
    completed_item = {
        **started_item,
        "aggregated_output": aggregated_output,
        "status": "failed" if failed else "completed",
    }
    if not failed:
        completed_item["exit_code"] = 0
    return started_item, completed_item


def _append_item_event(events: list[dict[str, Any]], event_type: str, item: dict[str, Any]) -> None:
    events.append({"type": event_type, "item": item})


def _build_codex_thread_events(
    *,
    thread_id: str,
    turn_id: str,
    runtime_events: list[dict[str, Any]],
    runtime_result: dict[str, Any] | None,
    error_message: str | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
    ]
    open_messages: dict[str, dict[str, Any]] = {}
    saw_completed_agent_message = False
    latest_todo_item: dict[str, Any] | None = None
    todo_started = False
    usage_by_step: dict[str, dict[str, int]] = {}

    def finalize_message(step_id: str | None) -> None:
        nonlocal saw_completed_agent_message
        if step_id is None:
            return
        item = open_messages.pop(step_id, None)
        if item is None:
            return
        _append_item_event(events, "item.completed", item)
        saw_completed_agent_message = True

    for runtime_event in runtime_events:
        if not isinstance(runtime_event, dict):
            continue
        event_type = str(runtime_event.get("type") or "")
        payload = runtime_event.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}

        if event_type == "delta":
            step_id = str(payload_dict.get("step_id") or "").strip()
            text = str(payload_dict.get("text") or "")
            if step_id == "" or text == "":
                continue
            item = open_messages.get(step_id)
            if item is None:
                item = {
                    "id": f"agent-message:{step_id}",
                    "type": "agent_message",
                    "text": text,
                }
                open_messages[step_id] = item
                _append_item_event(events, "item.started", dict(item))
                continue
            item["text"] = f"{item['text']}{text}"
            _append_item_event(events, "item.updated", dict(item))
            continue

        if event_type != "timeline_entry":
            continue

        entry_kind = str(payload_dict.get("entry_kind") or "").strip()
        step_id = str(payload_dict.get("step_id") or "").strip() or None
        finalize_message(step_id)

        if entry_kind == "planning":
            todo_item = {
                "id": f"todo-list:{turn_id}",
                "type": "todo_list",
                "items": _infer_todo_items(str(payload_dict.get("content_text") or "")),
            }
            latest_todo_item = todo_item
            if not todo_started:
                todo_started = True
                _append_item_event(events, "item.started", dict(todo_item))
            else:
                _append_item_event(events, "item.updated", dict(todo_item))
            continue

        if entry_kind != "action":
            continue

        content_json = payload_dict.get("content_json")
        if isinstance(content_json, str):
            try:
                content_json = json.loads(content_json)
            except json.JSONDecodeError:
                content_json = None
        content = content_json if isinstance(content_json, dict) else {}

        thought = str(content.get("thought") or "").strip()
        if thought:
            reasoning_item = {
                "id": f"reasoning:{step_id or uuid.uuid4().hex}",
                "type": "reasoning",
                "text": thought,
            }
            _append_item_event(events, "item.started", dict(reasoning_item))
            _append_item_event(events, "item.completed", reasoning_item)

        if step_id is not None and step_id not in usage_by_step:
            usage_payload = content.get("usage")
            if isinstance(usage_payload, dict):
                usage_by_step[step_id] = {
                    "input_tokens": int(usage_payload.get("inputTokens") or 0),
                    "output_tokens": int(usage_payload.get("outputTokens") or 0),
                }

        tool_results_by_call_id: dict[str, dict[str, Any]] = {}
        tool_results = content.get("toolResults")
        if isinstance(tool_results, list):
            for tool_result in tool_results:
                if not isinstance(tool_result, dict):
                    continue
                call_id = str(tool_result.get("callId") or "").strip()
                if call_id:
                    tool_results_by_call_id[call_id] = tool_result

        action_error = str(content.get("error") or "").strip()
        tool_calls = content.get("toolCalls")
        if not isinstance(tool_calls, list):
            continue

        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = str(tool_call.get("id") or uuid.uuid4().hex).strip()
            tool_name = str(tool_call.get("name") or "tool").strip()
            arguments = _parse_tool_arguments(tool_call)
            tool_result = tool_results_by_call_id.get(tool_call_id)
            failed = _tool_failed(tool_result, action_error)
            tool_error = _tool_error_text(tool_result, action_error)

            if tool_name == "write" and isinstance(arguments, dict) and isinstance(tool_result, dict):
                details = tool_result.get("details")
                if isinstance(details, dict):
                    mutation_kind = details.get("mutationKind")
                    if isinstance(mutation_kind, str) and mutation_kind in {"add", "update"}:
                        arguments = {**arguments, "mutationKind": mutation_kind}

            if tool_name in {"python_exec_agent", "grep", "find"}:
                started_item, completed_item = _command_item_for_tool(
                    tool_call_id,
                    tool_name,
                    arguments,
                    tool_result,
                    failed=failed,
                    error_text=tool_error,
                )
                _append_item_event(events, "item.started", started_item)
                _append_item_event(events, "item.completed", completed_item)
                continue

            if tool_name in {"write", "edit"}:
                file_item = _file_change_item_for_tool(tool_call_id, tool_name, arguments)
                file_item["status"] = "failed" if failed else "completed"
                _append_item_event(events, "item.completed", file_item)
                continue

            started_item = {
                "id": tool_call_id,
                "type": "mcp_tool_call",
                "server": "workspace",
                "tool": tool_name,
                "arguments": arguments,
                "status": "in_progress",
            }
            completed_item = _mcp_tool_item_for_tool(
                tool_call_id,
                tool_name,
                arguments,
                tool_result,
                failed=failed,
                error_text=tool_error,
            )
            _append_item_event(events, "item.started", started_item)
            _append_item_event(events, "item.completed", completed_item)

    for pending_step_id in list(open_messages.keys()):
        finalize_message(pending_step_id)

    final_answer = None
    if isinstance(runtime_result, dict):
        final_answer = str(runtime_result.get("final_answer") or "").strip() or None
    if final_answer and not saw_completed_agent_message:
        final_item = {
            "id": f"agent-message:final:{turn_id}",
            "type": "agent_message",
            "text": final_answer,
        }
        _append_item_event(events, "item.started", dict(final_item))
        _append_item_event(events, "item.completed", final_item)

    if latest_todo_item is not None:
        _append_item_event(events, "item.completed", latest_todo_item)

    if error_message:
        events.append({"type": "turn.failed", "error": {"message": error_message}})
        return events

    usage = {
        "input_tokens": sum(item.get("input_tokens", 0) for item in usage_by_step.values()),
        "cached_input_tokens": 0,
        "output_tokens": sum(item.get("output_tokens", 0) for item in usage_by_step.values()),
    }
    events.append({"type": "turn.completed", "usage": usage})
    return events


def _timeline_event_to_thread_event_row(
    *,
    thread_id: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    payload = event.get("payload")
    payload_dict = payload if isinstance(payload, dict) else {}
    run_id = str(event.get("turnId") or "")
    offset_raw = event.get("sequenceNo")
    try:
        offset = int(offset_raw)
    except Exception:
        offset = 0
    event_type = str(payload_dict.get("type") or event.get("eventType") or "unknown")
    return {
        "offset": offset,
        "run_id": run_id,
        "thread_id": thread_id,
        "event_type": event_type,
        "payload": payload_dict,
        "created_at": str(event.get("createdAt") or ""),
    }


def _list_thread_event_rows(
    *,
    session_service: SessionService,
    thread_id: str,
    from_offset: int = 0,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    payload = session_service.list_timeline_payload(thread_id)
    events = payload.get("events") if isinstance(payload, dict) else []
    rows: list[dict[str, Any]] = []
    for item in events if isinstance(events, list) else []:
        if not isinstance(item, dict):
            continue
        row = _timeline_event_to_thread_event_row(thread_id=thread_id, event=item)
        if int(row.get("offset") or 0) <= int(from_offset):
            continue
        if run_id and str(row.get("run_id") or "") != run_id:
            continue
        rows.append(row)
    return rows


def _handle_run_command(args: SimpleNamespace) -> int:
    _config, resolved, provider_id, model_id = _resolve_run_target(args)
    thread_id, metadata, resolved_thread = _resolve_thread_context(
        args,
        active_provider_id=provider_id,
        active_model_id=model_id,
    )
    workspace_id = str(resolved_thread.get("workspaceId") or "")
    run_id = f"run_{uuid.uuid4().hex}"
    _write_run_record(
        args.ns_bot_home,
        run_id,
        {
            "run_id": run_id,
            "thread_id": thread_id,
            "workspace_id": workspace_id,
            "prompt": str(args.user_input or ""),
            "workspace": str(metadata.workspace_path or args.workspace),
            "model": str(args.model or "").strip() or None,
            "status": "pending",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        },
    )

    error_message: str | None = None
    error_runtime_events: list[dict[str, Any]] = []

    if args.background:
        command: list[str] = [
            sys.argv[0],
            "--ns-bot-home",
            args.ns_bot_home,
        ]
        if str(args.db_path or "").strip():
            command.extend(["--db-path", str(args.db_path).strip()])
        command.extend(
            [
                "agent",
                "worker",
                "--run-id",
                run_id,
            ]
        )

        child = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _update_run_record(
            args.ns_bot_home,
            run_id,
            status="pending",
            pid=child.pid,
            updated_at=_now_iso(),
        )
        _print_json(
            {
                "run_id": run_id,
                "workspace_id": workspace_id,
                "thread_id": thread_id,
                "pid": child.pid,
                "status": "pending",
            }
        )
        return 0

    _update_run_record(
        args.ns_bot_home,
        run_id,
        status="running",
        updated_at=_now_iso(),
    )

    turn_output: dict[str, Any] | None = None
    try:
        turn_output = _execute_agent_turn(
            args=args,
            run_id=run_id,
            thread_id=thread_id,
            prompt=args.user_input,
            metadata=metadata,
            resolved=resolved,
        )
        _update_run_record(
            args.ns_bot_home,
            run_id,
            status="succeeded",
            updated_at=_now_iso(),
            finished_at=_now_iso(),
        )
    except Exception as exc:
        if isinstance(exc, _CliTurnExecutionError):
            error_runtime_events = exc.runtime_events
            error_message = str(exc)
        else:
            error_message = str(exc)
        _update_run_record(
            args.ns_bot_home,
            run_id,
            status="failed",
            updated_at=_now_iso(),
            finished_at=_now_iso(),
        )
        if not args.json:
            raise

    rows: list[dict[str, Any]] = []
    if bool(getattr(args, "include_timeline", False)):
        database, _repositories, session_service = _build_session_service(
            args.ns_bot_home,
            db_path=args.db_path,
        )
        try:
            rows = _list_thread_event_rows(
                session_service=session_service,
                thread_id=thread_id,
                from_offset=0,
                run_id=run_id,
            )
        finally:
            database.close()

    if args.json:
        codex_events = _build_codex_thread_events(
            thread_id=thread_id,
            turn_id=run_id,
            runtime_events=(turn_output or {}).get("runtimeEvents") or error_runtime_events,
            runtime_result=(turn_output or {}).get("result") if isinstance(turn_output, dict) else None,
            error_message=error_message,
        )
        payload = {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "thread_id": thread_id,
            "events": codex_events,
            "final_answer": (turn_output or {}).get("finalAnswer") if isinstance(turn_output, dict) else None,
            "error": error_message,
        }
        if bool(getattr(args, "include_timeline", False)):
            payload["timeline"] = rows
            payload["deprecated"] = {"timeline": _TIMELINE_DEPRECATION_NOTICE}
        _print_json(payload)
        return 0
    else:
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "workspace_id": workspace_id,
                    "thread_id": thread_id,
                },
                ensure_ascii=False,
            )
        )
        for row in rows:
            print(
                f"[{row['thread_id']}] {row['event_type']}: "
                f"{json.dumps(row['payload'], ensure_ascii=False)}"
            )
    return 0


def _handle_worker_command(args: SimpleNamespace) -> int:
    run_id = str(args.run_id or "").strip()
    if run_id == "":
        raise ValueError("Run id is required")
    run_record = _read_run_record(args.ns_bot_home, run_id)
    thread_id = str(run_record.get("thread_id") or "").strip()
    if thread_id == "":
        raise ValueError(f"Run thread is missing: {run_id}")
    prompt = str(run_record.get("prompt") or "").strip()
    if prompt == "":
        raise ValueError("Prompt is required")
    workspace = str(run_record.get("workspace") or "").strip() or str(
        args.workspace or os.getcwd()
    )
    model = str(run_record.get("model") or "").strip() or str(args.model or "").strip()

    pid_file = _thread_pid_file(args.ns_bot_home, thread_id)
    run_pid_file = _run_pid_file(args.ns_bot_home, run_id)
    _write_pid_file(pid_file, os.getpid())
    _write_pid_file(run_pid_file, os.getpid())
    _update_run_record(
        args.ns_bot_home,
        run_id,
        status="running",
        updated_at=_now_iso(),
        started_at=_now_iso(),
        pid=os.getpid(),
    )
    try:
        worker_args = SimpleNamespace(
            **vars(args),
            thread_id=thread_id,
            workspace=workspace,
            model=model,
        )
        _thread_id, metadata, _resolved_thread = _resolve_thread_context(
            worker_args,
            active_provider_id=None,
            active_model_id=None,
        )
        _execute_agent_turn(
            args=worker_args,
            run_id=run_id,
            thread_id=thread_id,
            prompt=prompt,
            metadata=metadata,
            resolved={"mode": "worker", "run_id": run_id},
        )
        _update_run_record(
            args.ns_bot_home,
            run_id,
            status="succeeded",
            updated_at=_now_iso(),
            finished_at=_now_iso(),
        )
        return 0
    except Exception:
        _update_run_record(
            args.ns_bot_home,
            run_id,
            status="failed",
            updated_at=_now_iso(),
            finished_at=_now_iso(),
        )
        raise
    finally:
        _unlink_pid_file_if_matches(pid_file, os.getpid())
        _unlink_pid_file_if_matches(run_pid_file, os.getpid())


def _handle_threads_list_command(args: SimpleNamespace) -> int:
    database, repositories, _session_service = _build_session_service(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        all_sessions: list[Any] = []
        for workspace in repositories.workspaces.list():
            all_sessions.extend(repositories.sessions.list_by_workspace_id(workspace.id))
        all_sessions.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)

        limit = max(1, int(args.limit or 20))
        sessions = all_sessions[:limit]
        payload = []
        workspace_by_id = {item.id: item for item in repositories.workspaces.list()}
        for session in sessions:
            workspace = workspace_by_id.get(session.workspace_id)
            pid_file = _thread_pid_file(args.ns_bot_home, session.id)
            payload.append(
                {
                    "threadId": session.id,
                    "workspace": workspace.real_path if workspace else None,
                    "status": _derive_thread_status(session=session, pid_file=pid_file),
                    "createdAt": session.created_at,
                    "updatedAt": session.updated_at,
                    "messageCount": session.message_count,
                }
            )
        _print_json({"threads": payload})
        return 0
    finally:
        database.close()


def _handle_thread_get_command(args: SimpleNamespace) -> int:
    database, repositories, _session_service = _build_session_service(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        thread_id = str(args.thread_id or "").strip()
        if thread_id == "":
            raise ValueError("Thread id is required")
        session = repositories.sessions.get_by_id(thread_id)
        workspace = repositories.workspaces.get_by_id(session.workspace_id)
        pid_file = _thread_pid_file(args.ns_bot_home, session.id)
        _print_json(
            {
                "threadId": session.id,
                "workspace": workspace.real_path,
                "status": _derive_thread_status(session=session, pid_file=pid_file),
                "sessionKey": session.session_key,
                "activeProviderId": session.active_provider_id,
                "activeModelId": session.active_model_id,
                "messageCount": session.message_count,
            }
        )
        return 0
    finally:
        database.close()


def _handle_thread_snapshot_command(args: SimpleNamespace) -> int:
    database, repositories, session_service = _build_session_service(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        thread_id = str(args.thread_id or "").strip()
        if thread_id == "":
            raise ValueError("Thread id is required")
        session = repositories.sessions.get_by_id(thread_id)
        workspace = repositories.workspaces.get_by_id(session.workspace_id)
        timeline = session_service.list_timeline_payload(session.id)
        _print_json(
            {
                "threadId": session.id,
                "workspace": workspace.real_path,
                "events": timeline.get("events", []),
                "pagination": timeline.get("pagination"),
            }
        )
        return 0
    finally:
        database.close()


def _handle_watch_command(args: SimpleNamespace) -> int:
    database, _repositories, session_service = _build_session_service(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        thread_id = str(args.thread_id or "").strip()
        if thread_id == "":
            raise ValueError("Thread id is required")
        from_offset = max(0, int(args.from_offset or 0))

        while True:
            rows = _list_thread_event_rows(
                session_service=session_service,
                thread_id=thread_id,
                from_offset=from_offset,
            )
            if args.json:
                _print_json(rows)
            else:
                for row in rows:
                    print(
                        f"[{row['offset']}][{row['thread_id']}] {row['event_type']}: "
                        f"{json.dumps(row['payload'], ensure_ascii=False)}"
                    )
            if not args.follow:
                return 0
            pid_file = _thread_pid_file(args.ns_bot_home, thread_id)
            if not pid_file.exists():
                return 0
            from_offset = (
                int(rows[-1].get("offset") or from_offset) if rows else from_offset
            )
            time.sleep(1)
    finally:
        database.close()


def _handle_cancel_command(args: SimpleNamespace) -> int:
    run_id = str(args.run_id or "").strip()
    if run_id == "":
        raise ValueError("Run id is required")
    run_record = _read_run_record(args.ns_bot_home, run_id)
    thread_id = str(run_record.get("thread_id") or "").strip()
    if thread_id == "":
        raise ValueError(f"Run thread is missing: {run_id}")

    run_pid_file = _run_pid_file(args.ns_bot_home, run_id)
    thread_pid_file = _thread_pid_file(args.ns_bot_home, thread_id)
    pid_raw = ""
    if run_pid_file.exists():
        pid_raw = run_pid_file.read_text(encoding="utf-8").strip()
    if pid_raw:
        os.kill(int(pid_raw), 15)
    run_pid_file.unlink(missing_ok=True)
    if thread_pid_file.exists():
        thread_pid_raw = thread_pid_file.read_text(encoding="utf-8").strip()
        if not pid_raw or thread_pid_raw == pid_raw:
            thread_pid_file.unlink(missing_ok=True)
    _update_run_record(
        args.ns_bot_home,
        run_id,
        status="canceled",
        updated_at=_now_iso(),
        finished_at=_now_iso(),
    )
    print("Canceled")
    return 0


def _handle_thread_delete_command(args: SimpleNamespace) -> int:
    database, repositories, session_service = _build_session_service(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        thread_id = str(args.thread_id or "").strip()
        if thread_id == "":
            raise ValueError("Thread id is required")
        repositories.sessions.get_by_id(thread_id)
        session_service.delete_session(thread_id)
        _print_json({"ok": True, "threadId": thread_id, "action": "deleted"})
        return 0
    finally:
        database.close()


def _ns_bot_home_from_ctx(ctx: typer.Context) -> str:
    return str(ctx.obj.get("ns_bot_home") if isinstance(ctx.obj, dict) else nsbot_home())


def _db_path_from_ctx(ctx: typer.Context) -> str | None:
    if not isinstance(ctx.obj, dict):
        return None
    value = str(ctx.obj.get("db_path") or "").strip()
    return value or None


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
    help="nsbot CLI",
    context_settings=HELP_OPTION_NAMES,
)
providers_app = typer.Typer(
    help="Manage providers",
    context_settings=HELP_OPTION_NAMES,
)
models_app = typer.Typer(
    help="Manage models",
    context_settings=HELP_OPTION_NAMES,
)
workspaces_app = typer.Typer(
    help="Manage workspaces",
    context_settings=HELP_OPTION_NAMES,
)
threads_app = typer.Typer(
    help="Manage threads",
    context_settings=HELP_OPTION_NAMES,
)
agent_app = typer.Typer(
    help="Agent commands: 'run' creates/schedules runs; 'worker' executes an existing run by run-id",
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
    db_path: str = typer.Option("", "--db-path", help="Override SQLite database path."),
    acp_mode: bool = typer.Option(
        False,
        "--acp",
        help="Start the sidecar in ACP stdio mode.",
    ),
) -> None:
    _ensure_templates_in_ns_bot_home(ns_bot_home_value)
    ctx.obj = {
        "ns_bot_home": ns_bot_home_value,
        "db_path": str(db_path or "").strip(),
        "acp": acp_mode,
    }


@providers_app.command("list")
def providers_list(ctx: typer.Context) -> None:
    _run_with_error_handling(
        lambda: _handle_providers_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=_db_path_from_ctx(ctx),
                providers_command="list",
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
                db_path=_db_path_from_ctx(ctx),
                providers_command="delete",
                provider_id=provider_id,
            )
        )
    )


@models_app.command("list")
def models_list(
    ctx: typer.Context,
    provider_id: str = typer.Option("", "--provider-id", help="Filter by provider id"),
    json_mode: bool = typer.Option(False, "--json"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_models_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                models_command="list",
                provider_id=provider_id,
                json=json_mode,
            )
        )
    )


@models_app.command("create")
def models_create(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name"),
    base_url: str = typer.Option(..., "--base-url"),
    model_id: str = typer.Option(..., "--model-id"),
    api_key: str = typer.Option(..., "--api-key"),
    json_mode: bool = typer.Option(False, "--json"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_models_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                models_command="create",
                name=name,
                base_url=base_url,
                model_id=model_id,
                api_key=api_key,
                json=json_mode,
            )
        )
    )


@models_app.command("get")
def models_get(
    ctx: typer.Context,
    identity: str = typer.Argument(...),
    json_mode: bool = typer.Option(False, "--json"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_models_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                models_command="get",
                identity=identity,
                json=json_mode,
            )
        )
    )


@models_app.command("set-default")
def models_set_default(
    ctx: typer.Context,
    identity: str = typer.Argument(...),
    json_mode: bool = typer.Option(False, "--json"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_models_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                models_command="set-default",
                identity=identity,
                json=json_mode,
            )
        )
    )


@models_app.command("remove")
def models_remove(
    ctx: typer.Context,
    provider_id: str = typer.Option("", "--provider-id"),
    model: str = typer.Option(..., "--model"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_models_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=_db_path_from_ctx(ctx),
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
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=_db_path_from_ctx(ctx),
                workspaces_command="list",
            )
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
                db_path=_db_path_from_ctx(ctx),
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
                db_path=_db_path_from_ctx(ctx),
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
                db_path=_db_path_from_ctx(ctx),
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
                db_path=_db_path_from_ctx(ctx),
                workspaces_command="sidecar-index-status",
                workspace_id=workspace_id,
            )
        )
    )


@threads_app.command("list")
def threads_list(
    ctx: typer.Context,
    archived: bool = typer.Option(False, "--archived"),
    limit: int = typer.Option(20, "--limit"),
    json_mode: bool = typer.Option(False, "--json"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_threads_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                threads_command="list",
                archived=archived,
                limit=limit,
                json=json_mode,
            )
        )
    )


@threads_app.command("get")
def threads_get(
    ctx: typer.Context,
    thread_id: str = typer.Option(..., "--thread-id"),
    json_mode: bool = typer.Option(False, "--json"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_threads_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                threads_command="get",
                thread_id=thread_id,
                json=json_mode,
            )
        )
    )


@threads_app.command("update")
def threads_update(
    ctx: typer.Context,
    thread_id: str = typer.Option(..., "--thread-id"),
    title: str = typer.Option(..., "--title"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_threads_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                threads_command="update",
                thread_id=thread_id,
                title=title,
            )
        )
    )


@threads_app.command("delete")
def threads_delete(
    ctx: typer.Context,
    thread_id: str = typer.Option(..., "--thread-id"),
    json_mode: bool = typer.Option(False, "--json"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_threads_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                threads_command="delete",
                thread_id=thread_id,
                json=json_mode,
            )
        )
    )


@agent_app.command("run")
def agent_run_command(
    ctx: typer.Context,
    prompt: str = typer.Option(..., "--prompt", help="Task prompt"),
    thread_id: str = typer.Option("", "--thread-id"),
    workspace: str = typer.Option(os.getcwd(), "--workspace", "-C"),
    model: str = typer.Option("", "--model"),
    background: bool = typer.Option(False, "--background"),
    json_mode: bool = typer.Option(False, "--json"),
    include_timeline: bool = typer.Option(
        False,
        "--include-timeline",
        help="Include deprecated ACP timeline compatibility rows in JSON output.",
    ),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_run_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                user_input=prompt,
                thread_id=thread_id,
                workspace=workspace,
                model=model,
                background=background,
                json=json_mode,
                include_timeline=include_timeline,
            )
        )
    )


@agent_app.command("worker")
def agent_worker_command(
    ctx: typer.Context,
    run_id: str = typer.Option(..., "--run-id"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_worker_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                run_id=run_id,
            )
        )
    )


@agent_app.command("watch")
def agent_watch_command(
    ctx: typer.Context,
    thread_id: str = typer.Option(..., "--thread-id"),
    from_offset: int = typer.Option(0, "--from-offset"),
    follow: bool = typer.Option(True, "--follow/--no-follow"),
    json_mode: bool = typer.Option(False, "--json"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_watch_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                thread_id=thread_id,
                from_offset=from_offset,
                follow=follow,
                json=json_mode,
            )
        )
    )


@agent_app.command("cancel")
def agent_cancel_command(
    ctx: typer.Context,
    run_id: str = typer.Option(..., "--run-id"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_cancel_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                run_id=run_id,
            )
        )
    )


@agent_app.command("thread-snapshot")
def agent_thread_snapshot_command(
    ctx: typer.Context,
    thread_id: str = typer.Option(..., "--thread-id"),
    json_mode: bool = typer.Option(False, "--json"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_thread_snapshot_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                thread_id=thread_id,
                json=json_mode,
            )
        )
    )


@agent_app.command("thread-delete")
def agent_thread_delete_command(
    ctx: typer.Context,
    thread_id: str = typer.Option(..., "--thread-id"),
    json_mode: bool = typer.Option(False, "--json"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_thread_delete_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                thread_id=thread_id,
                json=json_mode,
            )
        )
    )


app.add_typer(providers_app, name="providers")
app.add_typer(models_app, name="models")
app.add_typer(workspaces_app, name="workspaces")
app.add_typer(threads_app, name="threads")
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
                    "ACP mode cannot be combined with subcommands. Use 'nsbot --acp'."
                )
            resolved_ns_bot_home = ns_bot_home_value or str(nsbot_home())
            return _run_acp_mode(resolved_ns_bot_home)
        command.main(args=effective_argv, prog_name="nsbot", standalone_mode=False)
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
