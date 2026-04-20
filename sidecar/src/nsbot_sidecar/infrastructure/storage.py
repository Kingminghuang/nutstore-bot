from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Iterator

from nsbot_sidecar.infrastructure.local_paths import database_file_path, ensure_nsbot_root, ensure_secret_dir


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

CREATE TABLE IF NOT EXISTS providers (
        id TEXT PRIMARY KEY,
        runtime_provider TEXT NOT NULL,
        catalog_provider_id TEXT,
        display_name TEXT NOT NULL,
        base_url TEXT,
        secret_ref TEXT NOT NULL,
        preferred_model_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_providers_updated
ON providers(updated_at DESC, id ASC);

CREATE TABLE IF NOT EXISTS models (
        id TEXT PRIMARY KEY,
        provider_id TEXT NOT NULL,
        model_id TEXT NOT NULL,
        display_name TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_models_provider_model
ON models(provider_id, model_id);

CREATE INDEX IF NOT EXISTS idx_models_provider_model
ON models(provider_id, model_id);

CREATE TABLE IF NOT EXISTS default_model_selection (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
  active_provider_id TEXT,
  active_model_id TEXT,
    session_config_json TEXT NOT NULL DEFAULT '{}',
    session_meta_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
    last_message_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_workspace_updated
ON sessions(workspace_id, updated_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_sessions_updated
ON sessions(updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS acp_event_log (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  turn_id TEXT,
  sequence_no INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  event_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_acp_event_log_session_sequence
ON acp_event_log(session_id, sequence_no);

CREATE INDEX IF NOT EXISTS idx_acp_event_log_session_created
ON acp_event_log(session_id, created_at, sequence_no);

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
