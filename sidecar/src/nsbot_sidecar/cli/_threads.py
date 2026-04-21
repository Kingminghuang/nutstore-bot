from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any, Callable

from fastapi import HTTPException

from ._support import _http_detail
from nsbot_sidecar.application.session_service import SessionService


def history_event_to_thread_event_row(
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


def list_thread_event_rows(
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
        row = history_event_to_thread_event_row(thread_id=thread_id, event=item)
        if int(row.get("offset") or 0) <= int(from_offset):
            continue
        if run_id and str(row.get("run_id") or "") != run_id:
            continue
        rows.append(row)
    return rows


def handle_threads_command(
    args: SimpleNamespace,
    *,
    build_session_service: Callable[..., tuple[Any, Any, SessionService]],
    http_detail: Callable[[HTTPException], str],
    print_json: Callable[[Any], None],
    handle_threads_list_command: Callable[[SimpleNamespace], int],
    handle_thread_get_command: Callable[[SimpleNamespace], int],
    handle_thread_delete_command: Callable[[SimpleNamespace], int],
) -> int:
    if args.threads_command == "list":
        return handle_threads_list_command(args)

    if args.threads_command == "get":
        return handle_thread_get_command(args)

    if args.threads_command == "delete":
        return handle_thread_delete_command(args)

    if args.threads_command == "update":
        database, _repositories, session_service = build_session_service(
            args.ns_bot_home,
            db_path=args.db_path,
        )
        try:
            payload = {"title": str(args.title or "").strip()}
            try:
                updated = session_service.update_session(args.thread_id, payload)
            except HTTPException as exc:
                raise ValueError(http_detail(exc)) from exc
            print_json(updated)
            return 0
        finally:
            database.close()

    raise ValueError(f"Unknown threads command: {args.threads_command}")


def handle_threads_list_command(
    args: SimpleNamespace,
    *,
    build_session_service: Callable[..., tuple[Any, Any, SessionService]],
    thread_pid_file: Callable[..., Any],
    derive_thread_status: Callable[..., str],
    print_json: Callable[[Any], None],
) -> int:
    database, repositories, _session_service = build_session_service(
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
            pid_file = thread_pid_file(args.ns_bot_home, session.id)
            payload.append(
                {
                    "threadId": session.id,
                    "workspace": workspace.real_path if workspace else None,
                    "status": derive_thread_status(session=session, pid_file=pid_file),
                    "createdAt": session.created_at,
                    "updatedAt": session.updated_at,
                    "messageCount": session.message_count,
                }
            )
        print_json({"threads": payload})
        return 0
    finally:
        database.close()


def handle_thread_get_command(
    args: SimpleNamespace,
    *,
    build_session_service: Callable[..., tuple[Any, Any, SessionService]],
    thread_pid_file: Callable[..., Any],
    derive_thread_status: Callable[..., str],
    print_json: Callable[[Any], None],
) -> int:
    database, repositories, _session_service = build_session_service(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        thread_id = str(args.thread_id or "").strip()
        if thread_id == "":
            raise ValueError("Thread id is required")
        session = repositories.sessions.get_by_id(thread_id)
        workspace = repositories.workspaces.get_by_id(session.workspace_id)
        pid_file = thread_pid_file(args.ns_bot_home, session.id)
        print_json(
            {
                "threadId": session.id,
                "workspace": workspace.real_path,
                "status": derive_thread_status(session=session, pid_file=pid_file),
                "sessionKey": session.session_key,
                "activeProviderId": session.active_provider_id,
                "activeModelId": session.active_model_id,
                "messageCount": session.message_count,
            }
        )
        return 0
    finally:
        database.close()


def handle_thread_snapshot_command(
    args: SimpleNamespace,
    *,
    build_session_service: Callable[..., tuple[Any, Any, SessionService]],
    print_json: Callable[[Any], None],
) -> int:
    database, repositories, session_service = build_session_service(
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
        print_json(
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


def handle_watch_command(
    args: SimpleNamespace,
    *,
    build_session_service: Callable[..., tuple[Any, Any, SessionService]],
    thread_pid_file: Callable[..., Any],
    print_json: Callable[[Any], None],
    time_module: Any = time,
) -> int:
    database, _repositories, session_service = build_session_service(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        thread_id = str(args.thread_id or "").strip()
        if thread_id == "":
            raise ValueError("Thread id is required")
        from_offset = max(0, int(args.from_offset or 0))

        while True:
            rows = list_thread_event_rows(
                session_service=session_service,
                thread_id=thread_id,
                from_offset=from_offset,
            )
            if args.json:
                print_json(rows)
            else:
                for row in rows:
                    print(
                        f"[{row['offset']}][{row['thread_id']}] {row['event_type']}: "
                        f"{json.dumps(row['payload'], ensure_ascii=False)}"
                    )
            if not args.follow:
                return 0
            pid_file = thread_pid_file(args.ns_bot_home, thread_id)
            if not pid_file.exists():
                return 0
            from_offset = (
                int(rows[-1].get("offset") or from_offset) if rows else from_offset
            )
            time_module.sleep(1)
    finally:
        database.close()


def handle_thread_delete_command(
    args: SimpleNamespace,
    *,
    build_session_service: Callable[..., tuple[Any, Any, SessionService]],
    print_json: Callable[[Any], None],
) -> int:
    database, repositories, session_service = build_session_service(
        args.ns_bot_home,
        db_path=args.db_path,
    )
    try:
        thread_id = str(args.thread_id or "").strip()
        if thread_id == "":
            raise ValueError("Thread id is required")
        repositories.sessions.get_by_id(thread_id)
        session_service.delete_session(thread_id)
        print_json({"ok": True, "threadId": thread_id, "action": "deleted"})
        return 0
    finally:
        database.close()