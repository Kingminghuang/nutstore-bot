from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from typing import Any, cast

import click
from fastapi import HTTPException
import typer

from nsbot_sidecar.api.acp_app import AcpAppConfig
from nsbot_sidecar.application.provider_service import ProviderService
from nsbot_sidecar.application.session_service import SessionService
from nsbot_sidecar.application.timeline_service import TimelineService
from nsbot_sidecar.infrastructure.local_paths import nsbot_home
from nsbot_sidecar.infrastructure.repositories import create_repositories
from nsbot_sidecar.infrastructure.secret_store import LocalSecretStore
from nsbot_sidecar.infrastructure.storage import connect_database
from nsbot_sidecar.runtime.types import RuntimeWorkerConfig
from nsbot_sidecar.runtime.workspace_indexer import WorkspaceIndexer


HELP_OPTION_NAMES = {"help_option_names": ["-h", "--help"]}

TEMPLATE_REQUIRED_FILES = (
    "IDENTITFY.md",
    "SOUL.md",
    "USER.md",
    "TOOLS.md",
)


def _normalize_provider_ref(bundle: dict[str, Any]) -> str:
    return str(
        bundle.get("catalogProviderId")
        or bundle.get("customSlug")
        or bundle.get("runtimeProvider")
        or ""
    )


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


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
        workspace_indexer=WorkspaceIndexer(),
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