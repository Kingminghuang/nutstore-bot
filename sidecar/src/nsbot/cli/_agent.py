from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Callable

import uuid

from ._events import _TIMELINE_DEPRECATION_NOTICE, _build_codex_thread_events
from ._state import _now_iso
from nsbot.application.session_service import SessionService
from nsbot.runtime.types import RunMetadata, RuntimeWorkerConfig


def handle_run_command(
    args: SimpleNamespace,
    *,
    resolved_target: tuple[RuntimeWorkerConfig, dict[str, Any], str, str],
    resolve_thread_context: Callable[[SimpleNamespace], tuple[str, RunMetadata, dict[str, Any]]],
    execute_agent_turn: Callable[..., dict[str, Any]],
    cli_turn_execution_error_type: type[Exception],
    list_thread_event_rows: Callable[..., list[dict[str, Any]]],
    build_session_service: Callable[..., tuple[Any, Any, SessionService]],
    write_run_record: Callable[..., None],
    update_run_record: Callable[..., dict[str, Any]],
    print_json: Callable[[Any], None],
    subprocess_module: Any = subprocess,
    sys_module: Any = sys,
    uuid_module: Any = uuid,
) -> int:
    _config, resolved, _provider_id, _model_id = resolved_target
    thread_id, metadata, resolved_thread = resolve_thread_context(args)
    workspace_id = str(resolved_thread.get("workspaceId") or "")
    run_id = f"run_{uuid_module.uuid4().hex}"
    write_run_record(
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
            sys_module.argv[0],
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

        child = subprocess_module.Popen(
            command,
            stdout=subprocess_module.DEVNULL,
            stderr=subprocess_module.DEVNULL,
            start_new_session=True,
        )
        update_run_record(
            args.ns_bot_home,
            run_id,
            status="pending",
            pid=child.pid,
            updated_at=_now_iso(),
        )
        print_json(
            {
                "run_id": run_id,
                "workspace_id": workspace_id,
                "thread_id": thread_id,
                "pid": child.pid,
                "status": "pending",
            }
        )
        return 0

    update_run_record(
        args.ns_bot_home,
        run_id,
        status="running",
        updated_at=_now_iso(),
    )

    turn_output: dict[str, Any] | None = None
    try:
        turn_output = execute_agent_turn(
            args=args,
            run_id=run_id,
            thread_id=thread_id,
            prompt=args.user_input,
            metadata=metadata,
            resolved=resolved,
        )
        update_run_record(
            args.ns_bot_home,
            run_id,
            status="succeeded",
            updated_at=_now_iso(),
            finished_at=_now_iso(),
        )
    except Exception as exc:
        if isinstance(exc, cli_turn_execution_error_type):
            error_runtime_events = getattr(exc, "runtime_events", [])
            error_message = str(exc)
        else:
            error_message = str(exc)
        update_run_record(
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
        database, _repositories, session_service = build_session_service(
            args.ns_bot_home,
            db_path=args.db_path,
        )
        try:
            rows = list_thread_event_rows(
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
        print_json(payload)
        return 0

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


def handle_worker_command(
    args: SimpleNamespace,
    *,
    resolve_thread_context: Callable[[SimpleNamespace], tuple[str, RunMetadata, dict[str, Any]]],
    execute_agent_turn: Callable[..., dict[str, Any]],
    read_run_record: Callable[..., dict[str, Any]],
    thread_pid_file: Callable[..., Any],
    run_pid_file: Callable[..., Any],
    write_pid_file: Callable[..., None],
    update_run_record: Callable[..., dict[str, Any]],
    unlink_pid_file_if_matches: Callable[..., None],
    os_module: Any = os,
) -> int:
    run_id = str(args.run_id or "").strip()
    if run_id == "":
        raise ValueError("Run id is required")
    run_record = read_run_record(args.ns_bot_home, run_id)
    thread_id = str(run_record.get("thread_id") or "").strip()
    if thread_id == "":
        raise ValueError(f"Run thread is missing: {run_id}")
    prompt = str(run_record.get("prompt") or "").strip()
    if prompt == "":
        raise ValueError("Prompt is required")
    workspace = str(run_record.get("workspace") or "").strip() or str(
        args.workspace or os_module.getcwd()
    )
    model = str(run_record.get("model") or "").strip() or str(args.model or "").strip()

    pid_file = thread_pid_file(args.ns_bot_home, thread_id)
    current_run_pid_file = run_pid_file(args.ns_bot_home, run_id)
    write_pid_file(pid_file, os_module.getpid())
    write_pid_file(current_run_pid_file, os_module.getpid())
    update_run_record(
        args.ns_bot_home,
        run_id,
        status="running",
        updated_at=_now_iso(),
        started_at=_now_iso(),
        pid=os_module.getpid(),
    )
    try:
        worker_args = SimpleNamespace(
            **vars(args),
            thread_id=thread_id,
            workspace=workspace,
            model=model,
        )
        _thread_id, metadata, _resolved_thread = resolve_thread_context(worker_args)
        execute_agent_turn(
            args=worker_args,
            run_id=run_id,
            thread_id=thread_id,
            prompt=prompt,
            metadata=metadata,
            resolved={"mode": "worker", "run_id": run_id},
        )
        update_run_record(
            args.ns_bot_home,
            run_id,
            status="succeeded",
            updated_at=_now_iso(),
            finished_at=_now_iso(),
        )
        return 0
    except Exception:
        update_run_record(
            args.ns_bot_home,
            run_id,
            status="failed",
            updated_at=_now_iso(),
            finished_at=_now_iso(),
        )
        raise
    finally:
        unlink_pid_file_if_matches(pid_file, os_module.getpid())
        unlink_pid_file_if_matches(current_run_pid_file, os_module.getpid())


def handle_cancel_command(
    args: SimpleNamespace,
    *,
    read_run_record: Callable[..., dict[str, Any]],
    run_pid_file: Callable[..., Any],
    thread_pid_file: Callable[..., Any],
    update_run_record: Callable[..., dict[str, Any]],
    os_module: Any = os,
) -> int:
    run_id = str(args.run_id or "").strip()
    if run_id == "":
        raise ValueError("Run id is required")
    run_record = read_run_record(args.ns_bot_home, run_id)
    thread_id = str(run_record.get("thread_id") or "").strip()
    if thread_id == "":
        raise ValueError(f"Run thread is missing: {run_id}")

    current_run_pid_file = run_pid_file(args.ns_bot_home, run_id)
    current_thread_pid_file = thread_pid_file(args.ns_bot_home, thread_id)
    pid_raw = ""
    if current_run_pid_file.exists():
        pid_raw = current_run_pid_file.read_text(encoding="utf-8").strip()
    if pid_raw:
        os_module.kill(int(pid_raw), 15)
    current_run_pid_file.unlink(missing_ok=True)
    if current_thread_pid_file.exists():
        thread_pid_raw = current_thread_pid_file.read_text(encoding="utf-8").strip()
        if not pid_raw or thread_pid_raw == pid_raw:
            current_thread_pid_file.unlink(missing_ok=True)
    update_run_record(
        args.ns_bot_home,
        run_id,
        status="canceled",
        updated_at=_now_iso(),
        finished_at=_now_iso(),
    )
    print("Canceled")
    return 0