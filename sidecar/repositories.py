from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from storage import transaction


def now_iso_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def create_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def create_secret_ref(connection_id: str) -> str:
    return f"sec_{connection_id}"


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
class ProviderConnectionRecord:
    id: str
    kind: str
    runtime_provider: str
    catalog_provider_id: str | None
    custom_slug: str | None
    display_name: str
    base_url: str | None
    secret_ref: str
    api_key_configured: bool
    health_status: str
    health_message: str | None
    last_validated_at: str | None
    model_policy: str
    preferred_model_id: str | None
    is_enabled: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProviderModelRecord:
    id: str
    connection_id: str
    source: str
    model_id: str
    display_name: str | None
    enabled: bool
    sort_order: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProviderHeaderRecord:
    id: str
    connection_id: str
    name: str
    value_kind: str
    plain_value: str | None
    sort_order: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProviderConnectionBundle:
    connection: ProviderConnectionRecord
    models: list[ProviderModelRecord]
    headers: list[ProviderHeaderRecord]


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
    active_connection_id: str | None
    active_model_id: str | None
    created_at: str
    updated_at: str
    last_message_at: str | None


@dataclass(frozen=True)
class MessageRecord:
    id: str
    session_id: str
    run_id: str | None
    role: str
    content: str
    step_id: str | None
    sequence_no: int
    created_at: str
    metadata_json: str | None


@dataclass(frozen=True)
class RunRecord:
    id: str
    session_id: str
    workspace_id: str
    connection_id: str
    model_id: str
    status: str
    input_text: str
    final_answer: str | None
    error_code: str | None
    error_message: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    updated_at: str


@dataclass(frozen=True)
class RunStepRecord:
    id: str
    run_id: str
    session_id: str
    sequence_no: int
    step_id: str
    step_kind: str
    step_number: int | None
    plan_text: str | None
    code_action: str | None
    action_output_json: str | None
    observations_json: str
    error_text: str | None
    usage_json: str
    duration_ms: int
    has_delta: bool
    raw_model_output: str | None
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


class ProviderConnectionsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def save_bundle(
        self,
        *,
        connection_data: dict[str, object],
        models: list[dict[str, object]] | None = None,
        headers: list[dict[str, object]] | None = None,
    ) -> ProviderConnectionBundle:
        models = models or []
        headers = headers or []
        now = now_iso_timestamp()
        record_id = str(connection_data.get("id") or create_id("prov"))

        existing = self.get_bundle_by_id(record_id)
        created_at = existing.connection.created_at if existing else now
        secret_ref = str(
            connection_data.get("secret_ref")
            or (
                existing.connection.secret_ref
                if existing
                else create_secret_ref(record_id)
            )
        )

        with transaction(self.connection):
            self.connection.execute(
                """
                INSERT INTO provider_connections (
                    id, kind, runtime_provider, catalog_provider_id, custom_slug,
                    display_name, base_url, secret_ref, api_key_configured,
                    health_status, health_message, last_validated_at,
                    model_policy, preferred_model_id, is_enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    runtime_provider = excluded.runtime_provider,
                    catalog_provider_id = excluded.catalog_provider_id,
                    custom_slug = excluded.custom_slug,
                    display_name = excluded.display_name,
                    base_url = excluded.base_url,
                    secret_ref = excluded.secret_ref,
                    api_key_configured = excluded.api_key_configured,
                    health_status = excluded.health_status,
                    health_message = excluded.health_message,
                    last_validated_at = excluded.last_validated_at,
                    model_policy = excluded.model_policy,
                    preferred_model_id = excluded.preferred_model_id,
                    is_enabled = excluded.is_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    record_id,
                    str(connection_data["kind"]),
                    str(connection_data["runtime_provider"]),
                    connection_data.get("catalog_provider_id"),
                    connection_data.get("custom_slug"),
                    str(connection_data["display_name"]),
                    connection_data.get("base_url"),
                    secret_ref,
                    1 if bool(connection_data.get("api_key_configured", False)) else 0,
                    str(connection_data.get("health_status") or "unknown"),
                    connection_data.get("health_message"),
                    connection_data.get("last_validated_at"),
                    str(connection_data.get("model_policy") or "all_catalog"),
                    connection_data.get("preferred_model_id"),
                    0 if connection_data.get("is_enabled") is False else 1,
                    created_at,
                    now,
                ),
            )

            self.connection.execute(
                "DELETE FROM provider_models WHERE connection_id = ?", (record_id,)
            )
            self.connection.execute(
                "DELETE FROM provider_headers WHERE connection_id = ?", (record_id,)
            )

            for index, model in enumerate(models):
                self.connection.execute(
                    """
                    INSERT INTO provider_models (
                        id, connection_id, source, model_id, display_name,
                        enabled, sort_order, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(model.get("id") or create_id("pmod")),
                        record_id,
                        str(model["source"]),
                        str(model["model_id"]),
                        model.get("display_name"),
                        0 if model.get("enabled") is False else 1,
                        _as_int(model.get("sort_order", index)),
                        now,
                        now,
                    ),
                )

            for index, header in enumerate(headers):
                self.connection.execute(
                    """
                    INSERT INTO provider_headers (
                        id, connection_id, name, value_kind, plain_value,
                        sort_order, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(header.get("id") or create_id("hdr")),
                        record_id,
                        str(header["name"]),
                        str(header["value_kind"]),
                        header.get("plain_value"),
                        _as_int(header.get("sort_order", index)),
                        now,
                        now,
                    ),
                )

        return self.get_bundle_by_id_or_raise(record_id)

    def get_bundle_by_id(self, connection_id: str) -> ProviderConnectionBundle | None:
        row = self.connection.execute(
            "SELECT * FROM provider_connections WHERE id = ?", (connection_id,)
        ).fetchone()
        if row is None:
            return None

        models = self.connection.execute(
            "SELECT * FROM provider_models WHERE connection_id = ? ORDER BY sort_order ASC, model_id ASC",
            (connection_id,),
        ).fetchall()
        headers = self.connection.execute(
            "SELECT * FROM provider_headers WHERE connection_id = ? ORDER BY sort_order ASC, name ASC",
            (connection_id,),
        ).fetchall()

        return ProviderConnectionBundle(
            connection=_map_provider_connection(row),
            models=[_map_provider_model(model_row) for model_row in models],
            headers=[_map_provider_header(header_row) for header_row in headers],
        )

    def list_bundles(self) -> list[ProviderConnectionBundle]:
        rows = self.connection.execute(
            "SELECT id FROM provider_connections ORDER BY updated_at DESC, display_name ASC"
        ).fetchall()
        return [self.get_bundle_by_id_or_raise(str(row[0])) for row in rows]

    def delete_by_id(self, connection_id: str) -> None:
        self.connection.execute(
            "DELETE FROM provider_connections WHERE id = ?", (connection_id,)
        )
        self.connection.commit()

    def get_bundle_by_id_or_raise(self, connection_id: str) -> ProviderConnectionBundle:
        bundle = self.get_bundle_by_id(connection_id)
        if bundle is None:
            raise ValueError(f"Provider connection not found: {connection_id}")
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
        active_connection_id: str | None = None,
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
                active_connection_id, active_model_id, created_at, updated_at, last_message_at
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
                active_connection_id,
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
        payload = {
            "updated_at": updates.get("updated_at", now_iso_timestamp()),
            "last_message_at": updates.get("last_message_at", existing.last_message_at),
            "last_message_preview": updates.get(
                "last_message_preview", existing.last_message_preview
            ),
            "message_count": _as_int(
                updates.get("message_count", existing.message_count)
            ),
            "active_connection_id": updates.get(
                "active_connection_id", existing.active_connection_id
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
                active_connection_id = ?,
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
                payload["active_connection_id"],
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


class MessagesRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def append(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        message_id: str | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
        sequence_no: int | None = None,
        created_at: str | None = None,
        metadata_json: str | None = None,
    ) -> MessageRecord:
        record_id = message_id or create_id("msg")
        sequence = sequence_no or self._next_sequence_number(session_id)
        timestamp = created_at or now_iso_timestamp()
        self.connection.execute(
            """
            INSERT INTO messages (
                id, session_id, run_id, role, content, step_id, sequence_no, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                session_id,
                run_id,
                role,
                content,
                step_id,
                sequence,
                timestamp,
                metadata_json,
            ),
        )
        self.connection.commit()
        return self.get_by_id(record_id)

    def get_by_id(self, message_id: str) -> MessageRecord:
        row = self.connection.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Message not found: {message_id}")
        return _map_message(row)

    def list_by_session_id(self, session_id: str) -> list[MessageRecord]:
        rows = self.connection.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY sequence_no ASC, created_at ASC",
            (session_id,),
        ).fetchall()
        return [_map_message(row) for row in rows]

    def _next_sequence_number(self, session_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row[0]) + 1 if row is not None else 1


class RunsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(
        self,
        *,
        session_id: str,
        workspace_id: str,
        connection_id: str,
        model_id: str,
        input_text: str,
        run_id: str | None = None,
        status: str = "queued",
        final_answer: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> RunRecord:
        record_id = run_id or create_id("run")
        now = now_iso_timestamp()
        self.connection.execute(
            """
            INSERT INTO runs (
                id, session_id, workspace_id, connection_id, model_id, status,
                input_text, final_answer, error_code, error_message,
                created_at, started_at, completed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                session_id,
                workspace_id,
                connection_id,
                model_id,
                status,
                input_text,
                final_answer,
                error_code,
                error_message,
                now,
                started_at,
                completed_at,
                now,
            ),
        )
        self.connection.commit()
        return self.get_by_id(record_id)

    def get_by_id(self, run_id: str) -> RunRecord:
        row = self.connection.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Run not found: {run_id}")
        return _map_run(row)

    def update(self, run_id: str, **updates: object) -> RunRecord:
        self.connection.execute(
            """
            UPDATE runs
            SET status = ?, final_answer = ?, error_code = ?, error_message = ?,
                started_at = ?, completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                updates["status"],
                updates.get("final_answer"),
                updates.get("error_code"),
                updates.get("error_message"),
                updates.get("started_at"),
                updates.get("completed_at"),
                now_iso_timestamp(),
                run_id,
            ),
        )
        self.connection.commit()
        return self.get_by_id(run_id)


class RunStepsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(
        self,
        *,
        run_id: str,
        session_id: str,
        step_id: str,
        step_kind: str,
        sequence_no: int | None = None,
        step_number: int | None = None,
        plan_text: str | None = None,
        code_action: str | None = None,
        action_output_json: str | None = None,
        observations_json: str = "[]",
        error_text: str | None = None,
        usage_json: str = '{"inputTokens":0,"outputTokens":0,"reasoningTokens":0}',
        duration_ms: int = 0,
        has_delta: bool = False,
        raw_model_output: str | None = None,
        created_at: str | None = None,
        run_step_id: str | None = None,
    ) -> RunStepRecord:
        record_id = run_step_id or create_id("rstep")
        sequence = sequence_no or self._next_sequence_number(run_id)
        timestamp = created_at or now_iso_timestamp()
        self.connection.execute(
            """
            INSERT INTO run_steps (
                id, run_id, session_id, sequence_no, step_id, step_kind, step_number,
                plan_text, code_action, action_output_json, observations_json, error_text,
                usage_json, duration_ms, has_delta, raw_model_output, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                run_id,
                session_id,
                sequence,
                step_id,
                step_kind,
                step_number,
                plan_text,
                code_action,
                action_output_json,
                observations_json,
                error_text,
                usage_json,
                duration_ms,
                1 if has_delta else 0,
                raw_model_output,
                timestamp,
            ),
        )
        self.connection.commit()
        return self.get_by_id(record_id)

    def get_by_id(self, run_step_id: str) -> RunStepRecord:
        row = self.connection.execute(
            "SELECT * FROM run_steps WHERE id = ?", (run_step_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Run step not found: {run_step_id}")
        return _map_run_step(row)

    def list_by_run_id(self, run_id: str) -> list[RunStepRecord]:
        rows = self.connection.execute(
            "SELECT * FROM run_steps WHERE run_id = ? ORDER BY sequence_no ASC, created_at ASC",
            (run_id,),
        ).fetchall()
        return [_map_run_step(row) for row in rows]

    def _next_sequence_number(self, run_id: str) -> int:
        row = self.connection.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM run_steps WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row[0]) + 1 if row is not None else 1


@dataclass(frozen=True)
class Repositories:
    workspaces: WorkspacesRepository
    providers: ProviderConnectionsRepository
    sessions: SessionsRepository
    messages: MessagesRepository
    runs: RunsRepository
    run_steps: RunStepsRepository


def create_repositories(connection: sqlite3.Connection) -> Repositories:
    return Repositories(
        workspaces=WorkspacesRepository(connection),
        providers=ProviderConnectionsRepository(connection),
        sessions=SessionsRepository(connection),
        messages=MessagesRepository(connection),
        runs=RunsRepository(connection),
        run_steps=RunStepsRepository(connection),
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


def _map_provider_connection(row: sqlite3.Row) -> ProviderConnectionRecord:
    return ProviderConnectionRecord(
        id=str(row["id"]),
        kind=str(row["kind"]),
        runtime_provider=str(row["runtime_provider"]),
        catalog_provider_id=_nullable_str(row["catalog_provider_id"]),
        custom_slug=_nullable_str(row["custom_slug"]),
        display_name=str(row["display_name"]),
        base_url=_nullable_str(row["base_url"]),
        secret_ref=str(row["secret_ref"]),
        api_key_configured=bool(row["api_key_configured"]),
        health_status=str(row["health_status"]),
        health_message=_nullable_str(row["health_message"]),
        last_validated_at=_nullable_str(row["last_validated_at"]),
        model_policy=str(row["model_policy"]),
        preferred_model_id=_nullable_str(row["preferred_model_id"]),
        is_enabled=bool(row["is_enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _map_provider_model(row: sqlite3.Row) -> ProviderModelRecord:
    return ProviderModelRecord(
        id=str(row["id"]),
        connection_id=str(row["connection_id"]),
        source=str(row["source"]),
        model_id=str(row["model_id"]),
        display_name=_nullable_str(row["display_name"]),
        enabled=bool(row["enabled"]),
        sort_order=int(row["sort_order"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _map_provider_header(row: sqlite3.Row) -> ProviderHeaderRecord:
    return ProviderHeaderRecord(
        id=str(row["id"]),
        connection_id=str(row["connection_id"]),
        name=str(row["name"]),
        value_kind=str(row["value_kind"]),
        plain_value=_nullable_str(row["plain_value"]),
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
        active_connection_id=_nullable_str(row["active_connection_id"]),
        active_model_id=_nullable_str(row["active_model_id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_message_at=_nullable_str(row["last_message_at"]),
    )


def _map_message(row: sqlite3.Row) -> MessageRecord:
    return MessageRecord(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        run_id=_nullable_str(row["run_id"]),
        role=str(row["role"]),
        content=str(row["content"]),
        step_id=_nullable_str(row["step_id"]),
        sequence_no=int(row["sequence_no"]),
        created_at=str(row["created_at"]),
        metadata_json=_nullable_str(row["metadata_json"]),
    )


def _map_run(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        workspace_id=str(row["workspace_id"]),
        connection_id=str(row["connection_id"]),
        model_id=str(row["model_id"]),
        status=str(row["status"]),
        input_text=str(row["input_text"]),
        final_answer=_nullable_str(row["final_answer"]),
        error_code=_nullable_str(row["error_code"]),
        error_message=_nullable_str(row["error_message"]),
        created_at=str(row["created_at"]),
        started_at=_nullable_str(row["started_at"]),
        completed_at=_nullable_str(row["completed_at"]),
        updated_at=str(row["updated_at"]),
    )


def _map_run_step(row: sqlite3.Row) -> RunStepRecord:
    return RunStepRecord(
        id=str(row["id"]),
        run_id=str(row["run_id"]),
        session_id=str(row["session_id"]),
        sequence_no=int(row["sequence_no"]),
        step_id=str(row["step_id"]),
        step_kind=str(row["step_kind"]),
        step_number=None if row["step_number"] is None else int(row["step_number"]),
        plan_text=_nullable_str(row["plan_text"]),
        code_action=_nullable_str(row["code_action"]),
        action_output_json=_nullable_str(row["action_output_json"]),
        observations_json=str(row["observations_json"]),
        error_text=_nullable_str(row["error_text"]),
        usage_json=str(row["usage_json"]),
        duration_ms=int(row["duration_ms"]),
        has_delta=bool(row["has_delta"]),
        raw_model_output=_nullable_str(row["raw_model_output"]),
        created_at=str(row["created_at"]),
    )


def _nullable_str(value: object) -> str | None:
    return None if value is None else str(value)
