from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any
import uuid

import anyio
import click
from fastapi import HTTPException
import typer

from ._agent import (
    handle_cancel_command as _handle_cancel_command_impl,
    handle_run_command as _handle_run_command_impl,
    handle_worker_command as _handle_worker_command_impl,
)
from ._index_tasks import (
    handle_index_cancel_command as _handle_index_cancel_command_impl,
    handle_index_status_command as _handle_index_status_command_impl,
    handle_index_submit_command as _handle_index_submit_command_impl,
    handle_index_worker_command as _handle_index_worker_command_impl,
)
from ._models import handle_models_command as _handle_models_command_impl
from ._providers import (
    handle_providers_command as _handle_providers_command_impl,
)
from ._threads import (
    handle_thread_delete_command as _handle_thread_delete_command_impl,
    handle_thread_get_command as _handle_thread_get_command_impl,
    handle_thread_snapshot_command as _handle_thread_snapshot_command_impl,
    handle_threads_command as _handle_threads_command_impl,
    handle_threads_list_command as _handle_threads_list_command_impl,
    handle_watch_command as _handle_watch_command_impl,
    history_event_to_thread_event_row as _history_event_to_thread_event_row_impl,
    list_thread_event_rows as _list_thread_event_rows_impl,
)
from ._workspaces import (
    handle_workspaces_command as _handle_workspaces_command_impl,
)
from ._events import (
    _TIMELINE_DEPRECATION_NOTICE,
    _build_codex_thread_events,
)
from ._state import (
    _derive_thread_status,
    _find_active_index_task,
    _index_manifest_payload,
    _index_task_pid_file,
    _now_iso,
    _read_index_task_record,
    _read_run_record,
    _run_pid_file,
    _serialize_index_task_payload,
    _thread_pid_file,
    _unlink_pid_file_if_matches,
    _update_index_task_record,
    _update_run_record,
    _write_index_task_record,
    _write_pid_file,
    _write_run_record,
)
from ._support import (
    HELP_OPTION_NAMES,
    _build_acp_app_config,
    _build_runtime_target_resolution,
    _build_runtime_worker_config,
    _build_services,
    _build_session_service,
    _db_path_from_ctx,
    _ensure_templates_in_ns_bot_home,
    _find_target_group,
    _http_detail,
    _ns_bot_home_from_ctx,
    _parse_root_mode_arguments,
    _print_json,
    _run_acp_mode,
    _run_with_error_handling,
)
from nsbot.infrastructure.local_paths import nsbot_home
from nsbot.application.session_service import SessionService
from nsbot.runtime.engine import create_runtime_engine
from nsbot.runtime.types import RunMetadata, RuntimeWorkerConfig
from nsbot.runtime.workspace_indexer import WorkspaceIndexer


class _CliTurnExecutionError(RuntimeError):
    def __init__(self, message: str, *, runtime_events: list[dict[str, Any]]):
        super().__init__(message)
        self.runtime_events = runtime_events


def _handle_index_submit_command(args: SimpleNamespace) -> int:
    return _handle_index_submit_command_impl(
        args,
        build_session_service=_build_session_service,
        find_active_index_task=_find_active_index_task,
        index_manifest_payload=_index_manifest_payload,
        now_iso=_now_iso,
        write_index_task_record=_write_index_task_record,
        update_index_task_record=_update_index_task_record,
        serialize_index_task_payload=_serialize_index_task_payload,
        print_json=_print_json,
        subprocess_module=subprocess,
        sys_module=sys,
        uuid_module=uuid,
    )


def _handle_index_status_command(args: SimpleNamespace) -> int:
    return _handle_index_status_command_impl(
        args,
        build_session_service=_build_session_service,
        read_index_task_record=_read_index_task_record,
        index_manifest_payload=_index_manifest_payload,
        serialize_index_task_payload=_serialize_index_task_payload,
        print_json=_print_json,
    )


def _handle_index_cancel_command(args: SimpleNamespace) -> int:
    return _handle_index_cancel_command_impl(
        args,
        read_index_task_record=_read_index_task_record,
        index_task_pid_file=_index_task_pid_file,
        update_index_task_record=_update_index_task_record,
        now_iso=_now_iso,
        os_module=os,
    )


def _handle_index_worker_command(args: SimpleNamespace) -> int:
    return _handle_index_worker_command_impl(
        args,
        read_index_task_record=_read_index_task_record,
        index_task_pid_file=_index_task_pid_file,
        write_pid_file=_write_pid_file,
        update_index_task_record=_update_index_task_record,
        unlink_pid_file_if_matches=_unlink_pid_file_if_matches,
        now_iso=_now_iso,
        indexer_factory=WorkspaceIndexer,
        os_module=os,
    )


def _handle_threads_command(args: SimpleNamespace) -> int:
    return _handle_threads_command_impl(
        args,
        build_session_service=_build_session_service,
        http_detail=_http_detail,
        print_json=_print_json,
        handle_threads_list_command=_handle_threads_list_command,
        handle_thread_get_command=_handle_thread_get_command,
        handle_thread_delete_command=_handle_thread_delete_command,
    )


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
                _build_acp_app_config,
                _build_runtime_target_resolution,
                _build_runtime_worker_config,
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


def _history_event_to_thread_event_row(
    *,
    thread_id: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    return _history_event_to_thread_event_row_impl(thread_id=thread_id, event=event)


def _list_thread_event_rows(
    *,
    session_service: SessionService,
    thread_id: str,
    from_offset: int = 0,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    return _list_thread_event_rows_impl(
        session_service=session_service,
        thread_id=thread_id,
        from_offset=from_offset,
        run_id=run_id,
    )


def _handle_run_command(args: SimpleNamespace) -> int:
    resolved_target = _resolve_run_target(args)
    _config, _resolved, provider_id, model_id = resolved_target
    return _handle_run_command_impl(
        args,
        resolved_target=resolved_target,
        resolve_thread_context=lambda current_args: _resolve_thread_context(
            current_args,
            active_provider_id=provider_id,
            active_model_id=model_id,
        ),
        execute_agent_turn=_execute_agent_turn,
        cli_turn_execution_error_type=_CliTurnExecutionError,
        list_thread_event_rows=_list_thread_event_rows,
        build_session_service=_build_session_service,
        subprocess_module=subprocess,
        sys_module=sys,
        uuid_module=uuid,
        write_run_record=_write_run_record,
        update_run_record=_update_run_record,
        print_json=_print_json,
    )


def _handle_worker_command(args: SimpleNamespace) -> int:
    return _handle_worker_command_impl(
        args,
        resolve_thread_context=lambda current_args: _resolve_thread_context(
            current_args,
            active_provider_id=None,
            active_model_id=None,
        ),
        execute_agent_turn=_execute_agent_turn,
        read_run_record=_read_run_record,
        thread_pid_file=_thread_pid_file,
        run_pid_file=_run_pid_file,
        write_pid_file=_write_pid_file,
        update_run_record=_update_run_record,
        unlink_pid_file_if_matches=_unlink_pid_file_if_matches,
        os_module=os,
    )


def _handle_threads_list_command(args: SimpleNamespace) -> int:
    return _handle_threads_list_command_impl(
        args,
        build_session_service=_build_session_service,
        thread_pid_file=_thread_pid_file,
        derive_thread_status=_derive_thread_status,
        print_json=_print_json,
    )


def _handle_thread_get_command(args: SimpleNamespace) -> int:
    return _handle_thread_get_command_impl(
        args,
        build_session_service=_build_session_service,
        thread_pid_file=_thread_pid_file,
        derive_thread_status=_derive_thread_status,
        print_json=_print_json,
    )


def _handle_thread_snapshot_command(args: SimpleNamespace) -> int:
    return _handle_thread_snapshot_command_impl(
        args,
        build_session_service=_build_session_service,
        print_json=_print_json,
    )


def _handle_watch_command(args: SimpleNamespace) -> int:
    return _handle_watch_command_impl(
        args,
        build_session_service=_build_session_service,
        thread_pid_file=_thread_pid_file,
        print_json=_print_json,
        time_module=time,
    )


def _handle_cancel_command(args: SimpleNamespace) -> int:
    return _handle_cancel_command_impl(
        args,
        read_run_record=_read_run_record,
        run_pid_file=_run_pid_file,
        thread_pid_file=_thread_pid_file,
        update_run_record=_update_run_record,
        os_module=os,
    )


def _handle_thread_delete_command(args: SimpleNamespace) -> int:
    return _handle_thread_delete_command_impl(
        args,
        build_session_service=_build_session_service,
        print_json=_print_json,
    )


def _handle_providers_command(args: SimpleNamespace) -> int:
    return _handle_providers_command_impl(args)


def _handle_models_command(args: SimpleNamespace) -> int:
    return _handle_models_command_impl(args)


def _handle_workspaces_command(args: SimpleNamespace) -> int:
    return _handle_workspaces_command_impl(args)


MODEL_IDENTITY_HELP = (
    "Model identity. Use '<providerId>:<modelId>' or a bare '<modelId>' when it is unique."
)


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
index_app = typer.Typer(
    help="Manage explicit workspace index tasks",
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


@providers_app.command("create")
def providers_create(
    ctx: typer.Context,
    id: str = typer.Option(..., "--id", help="Builtin provider id"),
    api_key: str = typer.Option(..., "--api-key", help="Provider API key"),
    base_url: str | None = typer.Option(None, "--base-url", help="Override provider base URL"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_providers_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=_db_path_from_ctx(ctx),
                providers_command="create",
                id=id,
                api_key=api_key,
                base_url=base_url,
            )
        )
    )


@providers_app.command("get")
def providers_get(
    ctx: typer.Context,
    id: str = typer.Option(..., "--id", help="Provider id"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_providers_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=_db_path_from_ctx(ctx),
                providers_command="get",
                id=id,
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


@models_app.command(
    "create",
    help=(
        "Create a provider/model entry. The JSON result includes 'identity' in "
        "'<providerId>:<modelId>' format, which can be passed to 'models get', "
        "'models set-default', and 'models remove'."
    ),
)
def models_create(
    ctx: typer.Context,
    name: str = typer.Option("", "--name"),
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
    identity: str = typer.Argument(..., help=MODEL_IDENTITY_HELP),
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
    identity: str = typer.Argument(..., help=MODEL_IDENTITY_HELP),
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
    identity: str = typer.Argument(..., help=MODEL_IDENTITY_HELP),
    json_mode: bool = typer.Option(False, "--json"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_models_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                models_command="remove",
                identity=identity,
                json=json_mode,
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


@index_app.command("submit")
def workspaces_index_submit(
    ctx: typer.Context,
    workspace_id: str = typer.Option(..., "--workspace-id"),
    db_path: str = typer.Option("", "--db-path"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_index_submit_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=str(db_path or "").strip() or _db_path_from_ctx(ctx),
                workspace_id=workspace_id,
            )
        )
    )


@index_app.command("status")
def workspaces_index_status(
    ctx: typer.Context,
    task_id: str = typer.Option("", "--task-id"),
    workspace_id: str = typer.Option("", "--workspace-id"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_index_status_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                db_path=_db_path_from_ctx(ctx),
                task_id=task_id,
                workspace_id=workspace_id,
            )
        )
    )


@index_app.command("cancel")
def workspaces_index_cancel(
    ctx: typer.Context,
    task_id: str = typer.Option(..., "--task-id"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_index_cancel_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                task_id=task_id,
            )
        )
    )


@index_app.command("worker")
def workspaces_index_worker(
    ctx: typer.Context,
    task_id: str = typer.Option(..., "--task-id"),
) -> None:
    _run_with_error_handling(
        lambda: _handle_index_worker_command(
            SimpleNamespace(
                ns_bot_home=_ns_bot_home_from_ctx(ctx),
                task_id=task_id,
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
        "--include-deprecated-timeline",
        "--include-timeline",
        help="Include deprecated ACP timeline compatibility rows in JSON output. `--include-timeline` is kept as a compatibility alias.",
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
workspaces_app.add_typer(index_app, name="index")
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
