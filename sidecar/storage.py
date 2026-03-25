from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from local_paths import database_file_path, ensure_nsbot_root, ensure_secret_dir


@dataclass(frozen=True)
class StoragePaths:
    root: Path
    database: Path
    secrets_dir: Path


@dataclass(frozen=True)
class Migration:
    version: int
    sql: str


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        sql="""
        CREATE TABLE IF NOT EXISTS workspaces (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          path_label TEXT NOT NULL,
          real_path TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS provider_connections (
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL CHECK (kind IN ('builtin', 'custom')),
          runtime_provider TEXT NOT NULL CHECK (
            runtime_provider IN ('anthropic', 'deepseek', 'gemini', 'openai', 'custom')
          ),
          catalog_provider_id TEXT,
          custom_slug TEXT,
          display_name TEXT NOT NULL,
          base_url TEXT,
          secret_ref TEXT NOT NULL,
          api_key_configured INTEGER NOT NULL DEFAULT 0,
          model_policy TEXT NOT NULL DEFAULT 'all_catalog' CHECK (
            model_policy IN ('all_catalog', 'restricted', 'custom_only')
          ),
          preferred_model_id TEXT,
          is_enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_builtin_provider_connection
        ON provider_connections(catalog_provider_id)
        WHERE kind = 'builtin';

        CREATE TABLE IF NOT EXISTS provider_models (
          id TEXT PRIMARY KEY,
          connection_id TEXT NOT NULL,
          source TEXT NOT NULL CHECK (source IN ('catalog', 'custom')),
          model_id TEXT NOT NULL,
          display_name TEXT,
          enabled INTEGER NOT NULL DEFAULT 1,
          sort_order INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (connection_id) REFERENCES provider_connections(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_provider_models_connection
        ON provider_models(connection_id, sort_order, model_id);

        CREATE TABLE IF NOT EXISTS provider_headers (
          id TEXT PRIMARY KEY,
          connection_id TEXT NOT NULL,
          name TEXT NOT NULL,
          value_kind TEXT NOT NULL CHECK (value_kind IN ('plain', 'secret')),
          plain_value TEXT,
          sort_order INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (connection_id) REFERENCES provider_connections(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL,
          session_key TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          title_source TEXT NOT NULL CHECK (
            title_source IN ('placeholder', 'heuristic', 'model', 'manual')
          ),
          title_status TEXT NOT NULL DEFAULT 'idle' CHECK (
            title_status IN ('idle', 'pending', 'ready', 'failed')
          ),
          title_generation_attempts INTEGER NOT NULL DEFAULT 0,
          last_message_preview TEXT,
          message_count INTEGER NOT NULL DEFAULT 0,
          active_connection_id TEXT,
          active_model_id TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          last_message_at TEXT,
          FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
          FOREIGN KEY (active_connection_id) REFERENCES provider_connections(id)
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_workspace_updated
        ON sessions(workspace_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS messages (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          run_id TEXT,
          role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
          content TEXT NOT NULL,
          step_id TEXT,
          sequence_no INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          metadata_json TEXT,
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_messages_session_sequence
        ON messages(session_id, sequence_no);

        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          workspace_id TEXT NOT NULL,
          connection_id TEXT NOT NULL,
          model_id TEXT NOT NULL,
          status TEXT NOT NULL CHECK (
            status IN ('queued', 'running', 'completed', 'failed', 'cancelled')
          ),
          input_text TEXT NOT NULL,
          final_answer TEXT,
          error_code TEXT,
          error_message TEXT,
          created_at TEXT NOT NULL,
          started_at TEXT,
          completed_at TEXT,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
          FOREIGN KEY (connection_id) REFERENCES provider_connections(id)
        );
        """,
    ),
    Migration(
        version=2,
        sql="""
        ALTER TABLE provider_connections ADD COLUMN health_status TEXT NOT NULL DEFAULT 'unknown';
        ALTER TABLE provider_connections ADD COLUMN health_message TEXT;
        ALTER TABLE provider_connections ADD COLUMN last_validated_at TEXT;
        """,
    ),
    Migration(
        version=3,
        sql="""
        CREATE TABLE IF NOT EXISTS run_steps (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL,
          session_id TEXT NOT NULL,
          sequence_no INTEGER NOT NULL,
          step_id TEXT NOT NULL,
          step_kind TEXT NOT NULL CHECK (step_kind IN ('planning', 'action')),
          step_number INTEGER,
          plan_text TEXT,
          code_action TEXT,
          action_output_json TEXT,
          observations_json TEXT NOT NULL DEFAULT '[]',
          error_text TEXT,
          usage_json TEXT NOT NULL DEFAULT '{"inputTokens":0,"outputTokens":0,"reasoningTokens":0}',
          duration_ms INTEGER NOT NULL DEFAULT 0,
          has_delta INTEGER NOT NULL DEFAULT 0,
          raw_model_output TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE,
          FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_run_steps_run_sequence
        ON run_steps(run_id, sequence_no);

        CREATE INDEX IF NOT EXISTS idx_run_steps_run_created
        ON run_steps(run_id, created_at, sequence_no);
        """,
    ),
)


def prepare_storage(ns_bot_home: str | None = None) -> StoragePaths:
    root = ensure_nsbot_root(ns_bot_home)
    secrets_dir = ensure_secret_dir(ns_bot_home)
    return StoragePaths(
        root=root,
        database=database_file_path(ns_bot_home),
        secrets_dir=secrets_dir,
    )


def connect_database(
    ns_bot_home: str | None = None, db_path: str | None = None
) -> sqlite3.Connection:
    paths = prepare_storage(ns_bot_home)
    target_path = Path(db_path).expanduser().resolve() if db_path else paths.database
    connection = sqlite3.connect(target_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    connection.execute("PRAGMA journal_mode = WAL;")
    apply_migrations(connection)
    return connection


def apply_migrations(connection: sqlite3.Connection) -> None:
    current_version = get_user_version(connection)

    for migration in MIGRATIONS:
        if migration.version <= current_version:
            continue

        with transaction(connection):
            connection.executescript(migration.sql)
            connection.execute(f"PRAGMA user_version = {migration.version};")


def get_user_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version;").fetchone()
    if row is None:
        return 0
    return int(row[0])


def list_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
