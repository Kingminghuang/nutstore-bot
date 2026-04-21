from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Callable
import uuid


def handle_index_submit_command(
    args: SimpleNamespace,
    *,
    build_session_service: Callable[..., tuple[Any, Any, Any]],
    find_active_index_task: Callable[[str, str], dict[str, Any] | None],
    index_manifest_payload: Callable[[str, str], dict[str, Any]],
    now_iso: Callable[[], str],
    write_index_task_record: Callable[[str, str, dict[str, Any]], None],
    update_index_task_record: Callable[..., dict[str, Any]],
    serialize_index_task_payload: Callable[[dict[str, Any]], dict[str, Any]],
    print_json: Callable[[Any], None],
    subprocess_module: Any = subprocess,
    sys_module: Any = sys,
    uuid_module: Any = uuid,
) -> int:
    database, repositories, _session_service = build_session_service(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        workspace_id = str(args.workspace_id or "").strip()
        if workspace_id == "":
            raise ValueError("Workspace id is required")
        try:
            workspace = repositories.workspaces.get_by_id(workspace_id)
        except ValueError as exc:
            raise ValueError("Workspace not found") from exc

        existing = find_active_index_task(args.ns_bot_home, workspace_id)
        if existing is not None:
            existing["reused"] = True
            existing["manifest"] = index_manifest_payload(
                str(existing.get("workspaceId") or workspace.id),
                str(existing.get("workspacePath") or workspace.real_path),
            )
            print_json(serialize_index_task_payload(existing))
            return 0

        task_id = f"index_{uuid_module.uuid4().hex}"
        created_at = now_iso()
        payload = {
            "taskId": task_id,
            "workspaceId": workspace.id,
            "workspacePath": workspace.real_path,
            "status": "pending",
            "createdAt": created_at,
            "updatedAt": created_at,
            "startedAt": None,
            "finishedAt": None,
            "pid": None,
            "error": None,
            "reused": False,
            "manifest": index_manifest_payload(workspace.id, workspace.real_path),
        }
        write_index_task_record(args.ns_bot_home, task_id, payload)

        command: list[str] = [
            sys_module.argv[0],
            "--ns-bot-home",
            args.ns_bot_home,
        ]
        if str(args.db_path or "").strip():
            command.extend(["--db-path", str(args.db_path).strip()])
        command.extend(["workspaces", "index", "worker", "--task-id", task_id])

        child = subprocess_module.Popen(
            command,
            stdout=subprocess_module.DEVNULL,
            stderr=subprocess_module.DEVNULL,
            start_new_session=True,
        )
        updated = update_index_task_record(
            args.ns_bot_home,
            task_id,
            pid=child.pid,
            updatedAt=now_iso(),
        )
        print_json(serialize_index_task_payload(updated))
        return 0
    finally:
        database.close()


def handle_index_status_command(
    args: SimpleNamespace,
    *,
    build_session_service: Callable[..., tuple[Any, Any, Any]],
    read_index_task_record: Callable[[str, str], dict[str, Any]],
    index_manifest_payload: Callable[[str, str], dict[str, Any]],
    serialize_index_task_payload: Callable[[dict[str, Any]], dict[str, Any]],
    print_json: Callable[[Any], None],
) -> int:
    task_id = str(args.task_id or "").strip()
    workspace_id = str(args.workspace_id or "").strip()
    if task_id and workspace_id:
        raise ValueError("Use either --task-id or --workspace-id, not both")
    if not task_id and not workspace_id:
        raise ValueError("Either task id or workspace id is required")

    if workspace_id:
        database, _repositories, session_service = build_session_service(
            args.ns_bot_home,
            db_path=getattr(args, "db_path", None),
        )
        try:
            payload = session_service.workspace_index_status_payload(workspace_id)
            print_json(payload)
            return 0
        finally:
            database.close()

    payload = read_index_task_record(args.ns_bot_home, task_id)
    task_workspace_id = str(payload.get("workspaceId") or "")
    workspace_path = str(payload.get("workspacePath") or "")
    if task_workspace_id and workspace_path:
        payload["manifest"] = index_manifest_payload(task_workspace_id, workspace_path)
    print_json(serialize_index_task_payload(payload))
    return 0


def handle_index_cancel_command(
    args: SimpleNamespace,
    *,
    read_index_task_record: Callable[[str, str], dict[str, Any]],
    index_task_pid_file: Callable[[str, str], Any],
    update_index_task_record: Callable[..., dict[str, Any]],
    now_iso: Callable[[], str],
    os_module: Any = os,
) -> int:
    task_id = str(args.task_id or "").strip()
    if task_id == "":
        raise ValueError("Task id is required")

    task_record = read_index_task_record(args.ns_bot_home, task_id)
    pid_file = index_task_pid_file(args.ns_bot_home, task_id)
    pid_raw = ""
    if pid_file.exists():
        pid_raw = pid_file.read_text(encoding="utf-8").strip()
    if pid_raw:
        os_module.kill(int(pid_raw), 15)
    pid_file.unlink(missing_ok=True)
    update_index_task_record(
        args.ns_bot_home,
        task_id,
        status="canceled",
        updatedAt=now_iso(),
        finishedAt=now_iso(),
        pid=None,
        error=None,
        manifest=task_record.get("manifest"),
    )
    print("Canceled")
    return 0


def handle_index_worker_command(
    args: SimpleNamespace,
    *,
    read_index_task_record: Callable[[str, str], dict[str, Any]],
    index_task_pid_file: Callable[[str, str], Any],
    write_pid_file: Callable[[Any, int], None],
    update_index_task_record: Callable[..., dict[str, Any]],
    unlink_pid_file_if_matches: Callable[[Any, int], None],
    now_iso: Callable[[], str],
    indexer_factory: Callable[[], Any],
    os_module: Any = os,
) -> int:
    task_id = str(args.task_id or "").strip()
    if task_id == "":
        raise ValueError("Task id is required")

    payload = read_index_task_record(args.ns_bot_home, task_id)
    workspace_id = str(payload.get("workspaceId") or "").strip()
    workspace_path = str(payload.get("workspacePath") or "").strip()
    if workspace_id == "" or workspace_path == "":
        raise ValueError(f"Index task is missing workspace metadata: {task_id}")

    pid_file = index_task_pid_file(args.ns_bot_home, task_id)
    write_pid_file(pid_file, os_module.getpid())
    update_index_task_record(
        args.ns_bot_home,
        task_id,
        status="running",
        startedAt=now_iso(),
        updatedAt=now_iso(),
        pid=os_module.getpid(),
        error=None,
    )

    indexer = indexer_factory()
    try:
        indexer.index_workspace(workspace_id, workspace_path)
        manifest = indexer.status(workspace_id, workspace_path)
        update_index_task_record(
            args.ns_bot_home,
            task_id,
            status="succeeded",
            updatedAt=now_iso(),
            finishedAt=now_iso(),
            manifest=manifest,
            error=None,
        )
        return 0
    except Exception as exc:
        manifest = indexer.status(workspace_id, workspace_path)
        update_index_task_record(
            args.ns_bot_home,
            task_id,
            status="failed",
            updatedAt=now_iso(),
            finishedAt=now_iso(),
            manifest=manifest,
            error=str(exc),
        )
        raise
    finally:
        unlink_pid_file_if_matches(pid_file, os_module.getpid())