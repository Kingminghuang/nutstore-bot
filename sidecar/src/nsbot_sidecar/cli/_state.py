from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from nsbot_sidecar.runtime.workspace_indexer import WorkspaceIndexer


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


def _index_task_pid_file(ns_bot_home_value: str, task_id: str) -> Path:
    runtime_dir = Path(ns_bot_home_value).expanduser().resolve() / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / f"index-task-{task_id}.pid"


def _index_task_record_file(ns_bot_home_value: str, task_id: str) -> Path:
    runtime_dir = Path(ns_bot_home_value).expanduser().resolve() / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / f"index-task-{task_id}.json"


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


def _write_index_task_record(
    ns_bot_home_value: str, task_id: str, payload: dict[str, Any]
) -> None:
    path = _index_task_record_file(ns_bot_home_value, task_id)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_index_task_record(ns_bot_home_value: str, task_id: str) -> dict[str, Any]:
    path = _index_task_record_file(ns_bot_home_value, task_id)
    if not path.exists():
        raise ValueError(f"Index task not found: {task_id}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Index task metadata is corrupted: {task_id}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Index task metadata is invalid: {task_id}")
    return payload


def _update_index_task_record(
    ns_bot_home_value: str, task_id: str, **updates: Any
) -> dict[str, Any]:
    payload = _read_index_task_record(ns_bot_home_value, task_id)
    payload.update(updates)
    _write_index_task_record(ns_bot_home_value, task_id, payload)
    return payload


def _iter_index_task_records(ns_bot_home_value: str) -> list[dict[str, Any]]:
    runtime_dir = Path(ns_bot_home_value).expanduser().resolve() / "runtime"
    if not runtime_dir.exists():
        return []

    records: list[dict[str, Any]] = []
    for path in sorted(runtime_dir.glob("index-task-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            records.append(payload)

    records.sort(
        key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
        reverse=True,
    )
    return records


def _find_active_index_task(
    ns_bot_home_value: str, workspace_id: str
) -> dict[str, Any] | None:
    for payload in _iter_index_task_records(ns_bot_home_value):
        if str(payload.get("workspaceId") or "") != workspace_id:
            continue
        if str(payload.get("status") or "") not in {"pending", "running"}:
            continue
        return payload
    return None


def _index_manifest_payload(workspace_id: str, workspace_path: str) -> dict[str, Any]:
    return WorkspaceIndexer().status(workspace_id, workspace_path)


def _serialize_index_task_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = {
        "taskId": str(payload.get("taskId") or ""),
        "workspaceId": str(payload.get("workspaceId") or ""),
        "workspacePath": str(payload.get("workspacePath") or ""),
        "status": str(payload.get("status") or ""),
        "createdAt": payload.get("createdAt"),
        "updatedAt": payload.get("updatedAt"),
        "startedAt": payload.get("startedAt"),
        "finishedAt": payload.get("finishedAt"),
        "pid": payload.get("pid"),
        "error": payload.get("error"),
        "reused": bool(payload.get("reused", False)),
    }
    manifest_payload = payload.get("manifest")
    if isinstance(manifest_payload, dict):
        result["manifest"] = manifest_payload
    return result


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