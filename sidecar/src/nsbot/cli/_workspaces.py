from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from ._support import _build_session_service, _http_detail, _print_json


def handle_workspaces_command(args: SimpleNamespace) -> int:
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

        raise ValueError(f"Unknown workspaces command: {args.workspaces_command}")
    finally:
        database.close()