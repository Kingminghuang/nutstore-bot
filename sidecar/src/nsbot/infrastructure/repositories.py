from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any
from uuid import uuid4

from nsbot.infrastructure.storage import transaction


def now_iso_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def create_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def create_secret_ref(provider_id: str) -> str:
    return f"sec_{provider_id}"


def _as_int(value: object) -> int:
    try:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, (str, bytes, bytearray)):
            return int(value)
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class WorkspaceRecord:
    id: str
    name: str
    path_label: str
    real_path: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProviderRecord:
    id: str
    kind: str
    runtime_provider: str
    catalog_provider_id: str | None
    custom_slug: str | None
    display_name: str
    base_url: str | None
    secret_ref: str
    api_key_configured: bool
    model_policy: str
    preferred_model_id: str | None
    is_enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProviderModelRecord:
    id: str
    provider_id: str
    source: str
    model_id: str
    display_name: str | None
    enabled: bool
    sort_order: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProviderBundle:
    provider: ProviderRecord
    models: list[ProviderModelRecord]


@dataclass(frozen=True)
class DefaultModelSelectionRecord:
    provider_id: str
    model_id: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SessionRecord:
    id: str
    workspace_id: str
    session_key: str
    title: str
    title_source: str
    title_status: str
    title_generation_attempts: int
    last_message_preview: str | None
    message_count: int
    active_provider_id: str | None
    active_model_id: str | None
    session_config: dict[str, Any]
    session_meta: dict[str, Any]
    created_at: str
    updated_at: str
    last_message_at: str | None


@dataclass(frozen=True)
class AcpEventLogRecord:
    id: str
    session_id: str
    turn_id: str | None
    sequence_no: int
    event_type: str
    event_json: str
    created_at: str


class WorkspacesRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(
        self,
        name: str,
        path_label: str,
        real_path: str,
        workspace_id: str | None = None,
    ) -> WorkspaceRecord:
        record_id = workspace_id or create_id("ws")
        now = now_iso_timestamp()
        self.connection.execute(
            "INSERT INTO workspaces (id, name, path_label, real_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (record_id, name, path_label, real_path, now, now),
        )
        self.connection.commit()
        return self.get_by_id(record_id)

    def get_by_id(self, workspace_id: str) -> WorkspaceRecord:
        row = self.connection.execute(
            "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Workspace not found: {workspace_id}")
        return _map_workspace(row)

    def get_by_real_path(self, real_path: str) -> WorkspaceRecord | None:
        row = self.connection.execute(
            "SELECT * FROM workspaces WHERE real_path = ?",
            (real_path,),
        ).fetchone()
        return _map_workspace(row) if row is not None else None

    def list(self) -> list[WorkspaceRecord]:
        rows = self.connection.execute(
            "SELECT * FROM workspaces ORDER BY updated_at DESC, name ASC"
        ).fetchall()
        return [_map_workspace(row) for row in rows]

    def update(
        self,
        workspace_id: str,
        *,
        name: str | None = None,
        path_label: str | None = None,
        real_path: str | None = None,
    ) -> WorkspaceRecord:
        existing = self.get_by_id(workspace_id)
        self.connection.execute(
            """
            UPDATE workspaces
            SET name = ?, path_label = ?, real_path = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name or existing.name,
                path_label or existing.path_label,
                real_path or existing.real_path,
                now_iso_timestamp(),
                workspace_id,
            ),
        )
        self.connection.commit()
        return self.get_by_id(workspace_id)

    def delete_by_id(self, workspace_id: str) -> None:
        self.connection.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        self.connection.commit()


class ProvidersRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def save_bundle(
        self,
        *,
        provider_data: dict[str, object],
        models: list[dict[str, object]] | None = None,
    ) -> ProviderBundle:
        materialized_models = models if models is not None else None
        now = now_iso_timestamp()
        record_id = str(
            provider_data.get("id")
            or provider_data.get("catalog_provider_id")
            or provider_data.get("custom_slug")
            or provider_data.get("runtime_provider")
            or ""
        ).strip()
        if record_id == "":
            raise ValueError("Provider id could not be resolved")

        existing = self.get_bundle_by_id(record_id)
        created_at = existing.provider.created_at if existing else now
        secret_ref = str(
            provider_data.get("secret_ref")
            or (
                existing.provider.secret_ref
                if existing
                else create_secret_ref(record_id)
            )
        )

        with transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO providers (
                    id, runtime_provider, catalog_provider_id, display_name,
                    base_url, secret_ref, preferred_model_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    runtime_provider = excluded.runtime_provider,
                    catalog_provider_id = excluded.catalog_provider_id,
                    display_name = excluded.display_name,
                    base_url = excluded.base_url,
                    secret_ref = excluded.secret_ref,
                    preferred_model_id = excluded.preferred_model_id,
                    updated_at = excluded.updated_at
                """,
                (
                    record_id,
                    str(provider_data.get("runtime_provider") or "custom"),
                    provider_data.get("catalog_provider_id"),
                    str(provider_data.get("display_name") or record_id),
                    provider_data.get("base_url"),
                    secret_ref,
                    provider_data.get("preferred_model_id"),
                    created_at,
                    now,
                ),
            )

            if materialized_models is not None:
                self.connection.execute(
                    "DELETE FROM models WHERE provider_id = ?",
                    (record_id,),
                )
                for model in materialized_models:
                    model_id = str(model.get("model_id") or model.get("modelId") or "").strip()
                    if model_id == "":
                        continue
                    existing_model = None
                    if existing is not None:
                        existing_model = next(
                            (item for item in existing.models if item.model_id == model_id),
                            None,
                        )
                    self.connection.execute(
                        """
                        INSERT INTO models (
                            id, provider_id, model_id, display_name, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(model.get("id") or (existing_model.id if existing_model else create_id("model"))),
                            record_id,
                            model_id,
                            model.get("display_name") or model.get("displayName"),
                            existing_model.created_at if existing_model else now,
                            now,
                        ),
                    )

        return self.get_bundle_by_id_or_raise(record_id)

    def add_model(
        self,
        provider_id: str,
        *,
        model_id: str,
        display_name: str | None = None,
    ) -> ProviderModelRecord:
        provider = self.get_bundle_by_id(provider_id)
        if provider is None:
            raise ValueError(f"Provider not found: {provider_id}")
        now = now_iso_timestamp()
        record_id = create_id("model")
        self.connection.execute(
            """
            INSERT INTO models (id, provider_id, model_id, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (record_id, provider_id, model_id, display_name, now, now),
        )
        self.connection.commit()
        bundle = self.get_bundle_by_id_or_raise(provider_id)
        return next(item for item in bundle.models if item.id == record_id)

    def delete_model(self, provider_id: str, model_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM models WHERE provider_id = ? AND model_id = ?",
            (provider_id, model_id),
        )
        self.connection.commit()
        return int(cursor.rowcount or 0) > 0

    def get_bundle_by_id(self, provider_id: str) -> ProviderBundle | None:
        provider_row = self.connection.execute(
            "SELECT * FROM providers WHERE id = ?",
            (provider_id,),
        ).fetchone()
        if provider_row is None:
            return None

        provider = _map_provider(provider_row)
        model_rows = self.connection.execute(
            "SELECT * FROM models WHERE provider_id = ? ORDER BY created_at ASC, model_id ASC",
            (provider_id,),
        ).fetchall()

        return ProviderBundle(
            provider=provider,
            models=[
                _map_provider_model(model_row, provider=provider, sort_order=index)
                for index, model_row in enumerate(model_rows)
            ],
        )

    def list_bundles(self) -> list[ProviderBundle]:
        rows = self.connection.execute(
            "SELECT id FROM providers ORDER BY updated_at DESC, display_name ASC"
        ).fetchall()
        return [self.get_bundle_by_id_or_raise(str(row[0])) for row in rows]

    def delete_by_id(self, provider_id: str) -> None:
        with transaction(self.connection):
            self.connection.execute(
                "DELETE FROM models WHERE provider_id = ?",
                (provider_id,),
            )
            self.connection.execute(
                "DELETE FROM providers WHERE id = ?",
                (provider_id,),
            )

    def get_bundle_by_id_or_raise(self, provider_id: str) -> ProviderBundle:
        bundle = self.get_bundle_by_id(provider_id)
        if bundle is None:
            raise ValueError(f"Provider not found: {provider_id}")
        return bundle


class DefaultModelSelectionRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def get(self) -> DefaultModelSelectionRecord | None:
        row = self.connection.execute(
            "SELECT * FROM default_model_selection WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return _map_default_model_selection(row)

    def set(self, provider_id: str, model_id: str) -> DefaultModelSelectionRecord:
        existing = self.get()
        now = now_iso_timestamp()
        created_at = existing.created_at if existing is not None else now
        self.connection.execute(
            """
            INSERT INTO default_model_selection (id, provider_id, model_id, created_at, updated_at)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                provider_id = excluded.provider_id,
                model_id = excluded.model_id,
                updated_at = excluded.updated_at
            """,
            (provider_id, model_id, created_at, now),
        )
        self.connection.commit()
        record = self.get()
        if record is None:
            raise ValueError("Default model selection was not persisted")
        return record

    def clear(self) -> None:
        self.connection.execute("DELETE FROM default_model_selection WHERE id = 1")
        self.connection.commit()


class SessionsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(
        self,
        *,
        workspace_id: str,
        session_id: str | None = None,
        session_key: str | None = None,
        title: str = "New session",
        title_source: str = "placeholder",
        title_status: str = "idle",
        active_provider_id: str | None = None,
        active_model_id: str | None = None,
    ) -> SessionRecord:
        record_id = session_id or create_id("sess")
        session_key_value = session_key or record_id
        now = now_iso_timestamp()
        self.connection.execute(
            """
            INSERT INTO sessions (
                id, workspace_id, session_key, title, title_source, title_status,
                title_generation_attempts, last_message_preview, message_count,
                active_provider_id, active_model_id, session_config_json, session_meta_json,
                created_at, updated_at, last_message_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                workspace_id,
                session_key_value,
                title,
                title_source,
                title_status,
                0,
                None,
                0,
                active_provider_id,
                active_model_id,
                "{}",
                "{}",
                now,
                now,
                None,
            ),
        )
        self.connection.commit()
        return self.get_by_id(record_id)

    def get_by_id(self, session_id: str) -> SessionRecord:
        row = self.connection.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Session not found: {session_id}")
        return _map_session(row)

    def list_by_workspace_id(self, workspace_id: str) -> list[SessionRecord]:
        rows = self.connection.execute(
            "SELECT * FROM sessions WHERE workspace_id = ? ORDER BY updated_at DESC, id DESC",
            (workspace_id,),
        ).fetchall()
        return [_map_session(row) for row in rows]

    def list_page(
        self,
        *,
        limit: int,
        workspace_id: str | None = None,
        cursor_updated_at: str | None = None,
        cursor_id: str | None = None,
    ) -> tuple[list[SessionRecord], str | None]:
        where_parts: list[str] = []
        params: list[object] = []
        if workspace_id is not None:
            where_parts.append("workspace_id = ?")
            params.append(workspace_id)
        if cursor_updated_at is not None and cursor_id is not None:
            where_parts.append("(updated_at < ? OR (updated_at = ? AND id < ?))")
            params.extend([cursor_updated_at, cursor_updated_at, cursor_id])

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        params.append(limit + 1)
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM sessions
            {where_sql}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]
        records = [_map_session(row) for row in rows]
        next_cursor = records[-1].id if has_more and records else None
        return records, next_cursor

    def get_cursor_payload(self, session_id: str) -> tuple[str, str]:
        session = self.get_by_id(session_id)
        return session.updated_at, session.id

    def delete_by_id(self, session_id: str) -> None:
        self.connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.connection.commit()

    def update_title(
        self, session_id: str, title: str, title_source: str
    ) -> SessionRecord:
        self.connection.execute(
            "UPDATE sessions SET title = ?, title_source = ?, updated_at = ? WHERE id = ?",
            (title, title_source, now_iso_timestamp(), session_id),
        )
        self.connection.commit()
        return self.get_by_id(session_id)

    def touch(self, session_id: str, **updates: object) -> SessionRecord:
        existing = self.get_by_id(session_id)
        updated_at = updates.get("updated_at")
        payload = {
            "updated_at": updated_at if updated_at is not None else now_iso_timestamp(),
            "last_message_at": updates.get("last_message_at", existing.last_message_at),
            "last_message_preview": updates.get(
                "last_message_preview", existing.last_message_preview
            ),
            "message_count": _as_int(
                updates.get("message_count", existing.message_count)
            ),
            "active_provider_id": updates.get(
                "active_provider_id", existing.active_provider_id
            ),
            "active_model_id": updates.get("active_model_id", existing.active_model_id),
            "session_config_json": json.dumps(
                updates.get("session_config", existing.session_config),
                ensure_ascii=False,
                sort_keys=True,
            ),
            "session_meta_json": json.dumps(
                updates.get("session_meta", existing.session_meta),
                ensure_ascii=False,
                sort_keys=True,
            ),
            "title": updates.get("title", existing.title),
            "title_source": updates.get("title_source", existing.title_source),
            "title_status": updates.get("title_status", existing.title_status),
            "title_generation_attempts": _as_int(
                updates.get(
                    "title_generation_attempts", existing.title_generation_attempts
                )
            ),
        }

        self.connection.execute(
            """
            UPDATE sessions
            SET updated_at = ?,
                last_message_at = ?,
                last_message_preview = ?,
                message_count = ?,
                active_provider_id = ?,
                active_model_id = ?,
                session_config_json = ?,
                session_meta_json = ?,
                title = ?,
                title_source = ?,
                title_status = ?,
                title_generation_attempts = ?
            WHERE id = ?
            """,
            (
                payload["updated_at"],
                payload["last_message_at"],
                payload["last_message_preview"],
                payload["message_count"],
                payload["active_provider_id"],
                payload["active_model_id"],
                payload["session_config_json"],
                payload["session_meta_json"],
                payload["title"],
                payload["title_source"],
                payload["title_status"],
                payload["title_generation_attempts"],
                session_id,
            ),
        )
        self.connection.commit()
        return self.get_by_id(session_id)


class AcpEventLogRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def append(
        self,
        *,
        session_id: str,
        event_type: str,
        event_json: str,
        turn_id: str | None = None,
        event_id: str | None = None,
        sequence_no: int | None = None,
        created_at: str | None = None,
    ) -> AcpEventLogRecord:
        record_id = event_id or create_id("acpevt")
        sequence = sequence_no or self._next_sequence_number(session_id)
        timestamp = created_at or now_iso_timestamp()
        self.connection.execute(
            """
            INSERT INTO acp_event_log (
                id, session_id, turn_id, sequence_no, event_type, event_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                session_id,
                turn_id,
                sequence,
                event_type,
                event_json,
                timestamp,
            ),
        )
        self.connection.commit()
        return self.get_by_id(record_id)

    def get_by_id(self, event_id: str) -> AcpEventLogRecord:
        row = self.connection.execute(
            "SELECT * FROM acp_event_log WHERE id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"ACP event not found: {event_id}")
        return _map_acp_event(row)

    def list_by_session_id(self, session_id: str) -> list[AcpEventLogRecord]:
        rows = self.connection.execute(
            """
            SELECT * FROM acp_event_log
            WHERE session_id = ?
            ORDER BY sequence_no ASC, created_at ASC
            """,
            (session_id,),
        ).fetchall()
        return [_map_acp_event(row) for row in rows]

    def list_by_session_id_page(
        self,
        session_id: str,
        *,
        limit: int,
        before_sequence: int | None = None,
    ) -> tuple[list[AcpEventLogRecord], bool, int | None]:
        where_sql = "session_id = ?"
        params: list[object] = [session_id]
        if before_sequence is not None:
            where_sql += " AND sequence_no < ?"
            params.append(before_sequence)

        params.append(limit + 1)
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM acp_event_log
            WHERE {where_sql}
            ORDER BY sequence_no DESC, created_at DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        rows.reverse()
        records = [_map_acp_event(row) for row in rows]
        next_before_sequence = records[0].sequence_no if has_more and records else None
        return records, has_more, next_before_sequence

    def delete_by_session_id_from_sequence(
        self, session_id: str, from_sequence: int
    ) -> None:
        self.connection.execute(
            "DELETE FROM acp_event_log WHERE session_id = ? AND sequence_no >= ?",
            (session_id, from_sequence),
        )
        self.connection.commit()

    def delete_by_session_id(self, session_id: str) -> None:
        self.connection.execute(
            "DELETE FROM acp_event_log WHERE session_id = ?",
            (session_id,),
        )
        self.connection.commit()

    def _next_sequence_number(self, session_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM acp_event_log WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0]) + 1 if row is not None else 1


@dataclass(frozen=True)
class Repositories:
    workspaces: WorkspacesRepository
    providers: ProvidersRepository
    default_model_selection: DefaultModelSelectionRepository
    sessions: SessionsRepository
    acp_event_log: AcpEventLogRepository


def create_repositories(connection: sqlite3.Connection) -> Repositories:
    return Repositories(
        workspaces=WorkspacesRepository(connection),
        providers=ProvidersRepository(connection),
        default_model_selection=DefaultModelSelectionRepository(connection),
        sessions=SessionsRepository(connection),
        acp_event_log=AcpEventLogRepository(connection),
    )


def _map_workspace(row: sqlite3.Row) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=str(row["id"]),
        name=str(row["name"]),
        path_label=str(row["path_label"]),
        real_path=str(row["real_path"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _map_provider(row: sqlite3.Row) -> ProviderRecord:
    provider = str(row["id"])
    catalog_provider_id = _nullable_str(row["catalog_provider_id"])
    kind = "builtin" if catalog_provider_id else "custom"
    return ProviderRecord(
        id=provider,
        kind=kind,
        runtime_provider=str(row["runtime_provider"]),
        catalog_provider_id=catalog_provider_id,
        custom_slug=provider if kind == "custom" else None,
        display_name=str(row["display_name"]),
        base_url=_nullable_str(row["base_url"]),
        secret_ref=str(row["secret_ref"]),
        api_key_configured=True,
        model_policy="all_catalog" if kind == "builtin" else "custom_only",
        preferred_model_id=_nullable_str(row["preferred_model_id"]),
        is_enabled=True,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _map_provider_model(
    row: sqlite3.Row, *, provider: ProviderRecord, sort_order: int
) -> ProviderModelRecord:
    return ProviderModelRecord(
        id=str(row["id"]),
        provider_id=str(row["provider_id"]),
        source="catalog" if provider.catalog_provider_id else "custom",
        model_id=str(row["model_id"]),
        display_name=_nullable_str(row["display_name"]),
        enabled=True,
        sort_order=sort_order,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _map_default_model_selection(
    row: sqlite3.Row,
) -> DefaultModelSelectionRecord:
    return DefaultModelSelectionRecord(
        provider_id=str(row["provider_id"]),
        model_id=str(row["model_id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _map_session(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        session_key=str(row["session_key"]),
        title=str(row["title"]),
        title_source=str(row["title_source"]),
        title_status=str(row["title_status"]),
        title_generation_attempts=int(row["title_generation_attempts"]),
        last_message_preview=_nullable_str(row["last_message_preview"]),
        message_count=int(row["message_count"]),
        active_provider_id=_nullable_str(row["active_provider_id"]),
        active_model_id=_nullable_str(row["active_model_id"]),
        session_config=_json_object_or_empty(row["session_config_json"]),
        session_meta=_json_object_or_empty(row["session_meta_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_message_at=_nullable_str(row["last_message_at"]),
    )


def _map_acp_event(row: sqlite3.Row) -> AcpEventLogRecord:
    return AcpEventLogRecord(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        turn_id=_nullable_str(row["turn_id"]),
        sequence_no=int(row["sequence_no"]),
        event_type=str(row["event_type"]),
        event_json=str(row["event_json"]),
        created_at=str(row["created_at"]),
    )


def _json_object_or_empty(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _nullable_str(value: object) -> str | None:
    return None if value is None else str(value)
