from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Iterator

from local_paths import database_file_path, ensure_nsbot_root, ensure_secret_dir


@dataclass(frozen=True)
class StoragePaths:
    root: Path
    database: Path
    secrets_dir: Path


class ThreadLocalConnection:
    def __init__(self, database_path: Path):
        self._database_path = database_path
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_guard = threading.Lock()

    def _create_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.execute("PRAGMA journal_mode = WAL;")
        with self._connections_guard:
            self._connections.append(connection)
        return connection

    def _get_connection(self) -> sqlite3.Connection:
        existing = getattr(self._local, "connection", None)
        if isinstance(existing, sqlite3.Connection):
            return existing
        connection = self._create_connection()
        self._local.connection = connection
        return connection

    def execute(self, *args, **kwargs):
        return self._get_connection().execute(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        return self._get_connection().executescript(*args, **kwargs)

    def commit(self) -> None:
        self._get_connection().commit()

    def rollback(self) -> None:
        self._get_connection().rollback()

    def close(self) -> None:
        with self._connections_guard:
            connections = self._connections[:]
            self._connections.clear()
        for connection in connections:
            connection.close()
        self._local.__dict__.pop("connection", None)

    def __getattr__(self, name: str):
        return getattr(self._get_connection(), name)


SCHEMA_SQL = """
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
  health_status TEXT NOT NULL DEFAULT 'unknown',
  health_message TEXT,
  last_validated_at TEXT,
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

CREATE TABLE IF NOT EXISTS timeline_entries (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  run_id TEXT,
  sequence_no INTEGER NOT NULL,
  entry_kind TEXT NOT NULL CHECK (
    entry_kind IN ('user_input', 'planning', 'action', 'final_answer', 'system_notice')
  ),
  display_role TEXT NOT NULL CHECK (
    display_role IN ('user', 'assistant', 'system')
  ),
  step_id TEXT,
  step_number INTEGER,
  content_text TEXT,
  content_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_timeline_entries_session_sequence
ON timeline_entries(session_id, sequence_no);

CREATE INDEX IF NOT EXISTS idx_timeline_entries_session_created
ON timeline_entries(session_id, created_at, sequence_no);

CREATE INDEX IF NOT EXISTS idx_timeline_entries_run_sequence
ON timeline_entries(run_id, sequence_no, created_at);

CREATE TABLE IF NOT EXISTS attachments (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  file_name TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  storage_path TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('uploaded', 'consumed', 'deleted', 'missing')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_attachments_session_status
ON attachments(session_id, status, created_at);

CREATE TABLE IF NOT EXISTS draft_attachments (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  file_name TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  storage_path TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_draft_attachments_workspace_created
ON draft_attachments(workspace_id, created_at, id);
"""


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
) -> ThreadLocalConnection:
    paths = prepare_storage(ns_bot_home)
    target_path = Path(db_path).expanduser().resolve() if db_path else paths.database
    bootstrap_connection = sqlite3.connect(target_path, check_same_thread=False)
    bootstrap_connection.row_factory = sqlite3.Row
    bootstrap_connection.execute("PRAGMA foreign_keys = ON;")
    bootstrap_connection.execute("PRAGMA journal_mode = WAL;")
    try:
        initialize_schema(bootstrap_connection)
    finally:
        bootstrap_connection.close()
    return ThreadLocalConnection(target_path)


def initialize_schema(connection: sqlite3.Connection) -> None:
    with transaction(connection):
        connection.executescript(SCHEMA_SQL)


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
