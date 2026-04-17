from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from nsbot_sidecar.infrastructure.storage import transaction


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


@dataclass(frozen=True)
class AttachmentRecord:
    id: str
    session_id: str
    workspace_id: str
    file_name: str
    mime_type: str
    size_bytes: int
    storage_path: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DraftAttachmentRecord:
    id: str
    workspace_id: str
    file_name: str
    mime_type: str
    size_bytes: int
    storage_path: str
    created_at: str
    updated_at: str


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
        models = models or []
        now = now_iso_timestamp()
        kind = str(provider_data["kind"])
        record_id = str(
            provider_data.get("id")
            or (
                provider_data.get("catalog_provider_id")
                if kind == "builtin"
                else provider_data.get("custom_slug")
            )
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

        materialized_models = models[:]
        if not materialized_models:
            materialized_models = [
                {
                    "source": "catalog" if kind == "builtin" else "custom",
                    "model_id": "",
                    "display_name": None,
                    "enabled": False,
                    "sort_order": 0,
                }
            ]

        with transaction(self.connection):
            self.connection.execute("DELETE FROM models WHERE provider = ?", (record_id,))

            for index, model in enumerate(materialized_models):
                model_id = str(model.get("model_id") or "")
                existing_model = None
                if existing is not None:
                    existing_model = next(
                        (
                            item
                            for item in existing.models
                            if item.source == str(model["source"])
                            and item.model_id == model_id
                        ),
                        None,
                    )

                self.connection.execute(
                    """
                    INSERT INTO models (
                        id, provider, kind, provider_display_name, base_url,
                        secret_ref, api_key_configured, model_policy,
                        preferred_model_id, is_enabled, source, model_id,
                        display_name, enabled, sort_order, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(model.get("id") or (existing_model.id if existing_model else create_id("model"))),
                        record_id,
                        kind,
                        str(provider_data["display_name"]),
                        provider_data.get("base_url"),
                        secret_ref,
                        1 if bool(provider_data.get("api_key_configured", False)) else 0,
                        str(provider_data.get("model_policy") or "all_catalog"),
                        provider_data.get("preferred_model_id"),
                        0 if provider_data.get("is_enabled") is False else 1,
                        str(model["source"]),
                        model_id,
                        model.get("display_name"),
                        0 if model.get("enabled") is False else 1,
                        _as_int(model.get("sort_order", index)),
                        existing_model.created_at if existing_model else created_at,
                        now,
                    ),
                )

        return self.get_bundle_by_id_or_raise(record_id)

    def get_bundle_by_id(self, provider_id: str) -> ProviderBundle | None:
        row = self.connection.execute(
            "SELECT * FROM models WHERE provider = ? ORDER BY sort_order ASC, model_id ASC LIMIT 1",
            (provider_id,),
        ).fetchone()
        if row is None:
            return None

        models = self.connection.execute(
            "SELECT * FROM models WHERE provider = ? ORDER BY sort_order ASC, model_id ASC",
            (provider_id,),
        ).fetchall()

        return ProviderBundle(
            provider=_map_provider(row),
            models=[_map_provider_model(model_row) for model_row in models],
        )

    def list_bundles(self) -> list[ProviderBundle]:
        rows = self.connection.execute(
            "SELECT provider FROM models GROUP BY provider ORDER BY MAX(updated_at) DESC, MIN(provider_display_name) ASC"
        ).fetchall()
        return [self.get_bundle_by_id_or_raise(str(row[0])) for row in rows]

    def delete_by_id(self, provider_id: str) -> None:
        self.connection.execute("DELETE FROM models WHERE provider = ?", (provider_id,))
        self.connection.commit()

    def get_bundle_by_id_or_raise(self, provider_id: str) -> ProviderBundle:
        bundle = self.get_bundle_by_id(provider_id)
        if bundle is None:
            raise ValueError(f"Provider not found: {provider_id}")
        return bundle


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
                active_provider_id, active_model_id, created_at, updated_at, last_message_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            "SELECT * FROM sessions WHERE workspace_id = ? ORDER BY updated_at DESC, created_at DESC",
            (workspace_id,),
        ).fetchall()
        return [_map_session(row) for row in rows]

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

    def _next_sequence_number(self, session_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM acp_event_log WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0]) + 1 if row is not None else 1


class AttachmentsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(
        self,
        *,
        session_id: str,
        workspace_id: str,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        storage_path: str,
        status: str = "uploaded",
        attachment_id: str | None = None,
    ) -> AttachmentRecord:
        record_id = attachment_id or create_id("att")
        now = now_iso_timestamp()
        self.connection.execute(
            """
            INSERT INTO attachments (
                id, session_id, workspace_id, file_name, mime_type,
                size_bytes, storage_path, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                session_id,
                workspace_id,
                file_name,
                mime_type,
                size_bytes,
                storage_path,
                status,
                now,
                now,
            ),
        )
        self.connection.commit()
        return self.get_by_id(record_id)

    def get_by_id(self, attachment_id: str) -> AttachmentRecord:
        row = self.connection.execute(
            "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Attachment not found: {attachment_id}")
        return _map_attachment(row)

    def list_by_session_id(
        self, session_id: str, *, statuses: list[str] | None = None
    ) -> list[AttachmentRecord]:
        if statuses is None:
            rows = self.connection.execute(
                """
                SELECT * FROM attachments
                WHERE session_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            ).fetchall()
            return [_map_attachment(row) for row in rows]

        placeholders = ",".join("?" for _ in statuses)
        rows = self.connection.execute(
            f"""
            SELECT * FROM attachments
            WHERE session_id = ? AND status IN ({placeholders})
            ORDER BY created_at ASC, id ASC
            """,
            (session_id, *statuses),
        ).fetchall()
        return [_map_attachment(row) for row in rows]

    def list_by_ids(self, attachment_ids: list[str]) -> list[AttachmentRecord]:
        if not attachment_ids:
            return []
        placeholders = ",".join("?" for _ in attachment_ids)
        rows = self.connection.execute(
            f"SELECT * FROM attachments WHERE id IN ({placeholders})",
            tuple(attachment_ids),
        ).fetchall()
        return [_map_attachment(row) for row in rows]

    def update_status(self, attachment_id: str, status: str) -> AttachmentRecord:
        self.connection.execute(
            "UPDATE attachments SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso_timestamp(), attachment_id),
        )
        self.connection.commit()
        return self.get_by_id(attachment_id)

    def delete_by_id(self, attachment_id: str) -> None:
        self.connection.execute(
            "DELETE FROM attachments WHERE id = ?", (attachment_id,)
        )
        self.connection.commit()


class DraftAttachmentsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(
        self,
        *,
        workspace_id: str,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        storage_path: str,
        draft_attachment_id: str | None = None,
    ) -> DraftAttachmentRecord:
        record_id = draft_attachment_id or create_id("draftatt")
        now = now_iso_timestamp()
        self.connection.execute(
            """
            INSERT INTO draft_attachments (
                id, workspace_id, file_name, mime_type, size_bytes, storage_path, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                workspace_id,
                file_name,
                mime_type,
                size_bytes,
                storage_path,
                now,
                now,
            ),
        )
        self.connection.commit()
        return self.get_by_id(record_id)

    def get_by_id(self, draft_attachment_id: str) -> DraftAttachmentRecord:
        row = self.connection.execute(
            "SELECT * FROM draft_attachments WHERE id = ?",
            (draft_attachment_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Draft attachment not found: {draft_attachment_id}")
        return _map_draft_attachment(row)

    def list_by_workspace_id(self, workspace_id: str) -> list[DraftAttachmentRecord]:
        rows = self.connection.execute(
            """
            SELECT * FROM draft_attachments
            WHERE workspace_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (workspace_id,),
        ).fetchall()
        return [_map_draft_attachment(row) for row in rows]

    def list_by_ids(
        self, draft_attachment_ids: list[str]
    ) -> list[DraftAttachmentRecord]:
        if not draft_attachment_ids:
            return []
        placeholders = ",".join("?" for _ in draft_attachment_ids)
        rows = self.connection.execute(
            f"SELECT * FROM draft_attachments WHERE id IN ({placeholders})",
            tuple(draft_attachment_ids),
        ).fetchall()
        return [_map_draft_attachment(row) for row in rows]

    def delete_by_id(self, draft_attachment_id: str) -> None:
        self.connection.execute(
            "DELETE FROM draft_attachments WHERE id = ?",
            (draft_attachment_id,),
        )
        self.connection.commit()


@dataclass(frozen=True)
class Repositories:
    workspaces: WorkspacesRepository
    providers: ProvidersRepository
    sessions: SessionsRepository
    acp_event_log: AcpEventLogRepository
    attachments: AttachmentsRepository
    draft_attachments: DraftAttachmentsRepository


def create_repositories(connection: sqlite3.Connection) -> Repositories:
    return Repositories(
        workspaces=WorkspacesRepository(connection),
        providers=ProvidersRepository(connection),
        sessions=SessionsRepository(connection),
        acp_event_log=AcpEventLogRepository(connection),
        attachments=AttachmentsRepository(connection),
        draft_attachments=DraftAttachmentsRepository(connection),
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
    kind = str(row["kind"])
    provider = str(row["provider"])
    return ProviderRecord(
        id=provider,
        kind=kind,
        runtime_provider=provider if kind == "builtin" else "custom",
        catalog_provider_id=provider if kind == "builtin" else None,
        custom_slug=provider if kind == "custom" else None,
        display_name=str(row["provider_display_name"]),
        base_url=_nullable_str(row["base_url"]),
        secret_ref=str(row["secret_ref"]),
        api_key_configured=bool(row["api_key_configured"]),
        model_policy=str(row["model_policy"]),
        preferred_model_id=_nullable_str(row["preferred_model_id"]),
        is_enabled=bool(row["is_enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _map_provider_model(row: sqlite3.Row) -> ProviderModelRecord:
    return ProviderModelRecord(
        id=str(row["id"]),
        provider_id=str(row["provider"]),
        source=str(row["source"]),
        model_id=str(row["model_id"]),
        display_name=_nullable_str(row["display_name"]),
        enabled=bool(row["enabled"]),
        sort_order=int(row["sort_order"]),
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


def _map_attachment(row: sqlite3.Row) -> AttachmentRecord:
    return AttachmentRecord(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        workspace_id=str(row["workspace_id"]),
        file_name=str(row["file_name"]),
        mime_type=str(row["mime_type"]),
        size_bytes=int(row["size_bytes"]),
        storage_path=str(row["storage_path"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _map_draft_attachment(row: sqlite3.Row) -> DraftAttachmentRecord:
    return DraftAttachmentRecord(
        id=str(row["id"]),
        workspace_id=str(row["workspace_id"]),
        file_name=str(row["file_name"]),
        mime_type=str(row["mime_type"]),
        size_bytes=int(row["size_bytes"]),
        storage_path=str(row["storage_path"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _nullable_str(value: object) -> str | None:
    return None if value is None else str(value)
