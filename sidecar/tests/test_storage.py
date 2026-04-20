from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import shutil
import tempfile
import threading
import unittest

from nsbot_sidecar.infrastructure.storage import (
    connect_database,
    list_tables,
    prepare_storage,
)


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="sidecar-storage-")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_prepare_storage_creates_expected_paths(self) -> None:
        paths = prepare_storage(self.temp_dir)
        self.assertTrue(paths.root.exists())
        self.assertTrue(paths.secrets_dir.exists())
        self.assertTrue(str(paths.database).endswith("sidecar.db"))

    def test_connect_database_initializes_schema(self) -> None:
        connection = connect_database(self.temp_dir)
        try:
            tables = list_tables(connection)
            self.assertIn("workspaces", tables)
            self.assertIn("models", tables)
            self.assertIn("sessions", tables)
            self.assertIn("acp_event_log", tables)
            self.assertNotIn("runs", tables)

            model_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(models)").fetchall()
            }
            self.assertIn("provider_id", model_columns)
            self.assertIn("model_id", model_columns)
            self.assertNotIn("health_status", model_columns)
            self.assertNotIn("health_message", model_columns)
            self.assertNotIn("last_validated_at", model_columns)

            session_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            self.assertIn("session_config_json", session_columns)
            self.assertIn("session_meta_json", session_columns)

            self.assertEqual(
                connection.execute("PRAGMA foreign_key_list(sessions)").fetchall(),
                [],
            )
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_list(acp_event_log)").fetchall(),
                [],
            )
        finally:
            connection.close()

    def test_connect_database_uses_distinct_connections_per_thread(self) -> None:
        connection = connect_database(self.temp_dir)
        try:
            connection.execute(
                "INSERT INTO workspaces (id, name, path_label, real_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "ws_main",
                    "main",
                    "/tmp/main",
                    "/tmp/main",
                    "2026-03-31T00:00:00Z",
                    "2026-03-31T00:00:00Z",
                ),
            )
            connection.commit()

            main_thread_connection_id = id(connection._get_connection())
            barrier = threading.Barrier(3)

            def read_from_worker(worker_id: int) -> tuple[int, int]:
                barrier.wait()
                row = connection.execute(
                    "SELECT COUNT(*) FROM workspaces WHERE id = ?", ("ws_main",)
                ).fetchone()
                self.assertIsNotNone(row)
                return id(connection._get_connection()), int(row[0])

            with ThreadPoolExecutor(max_workers=2) as executor:
                future_one = executor.submit(read_from_worker, 1)
                future_two = executor.submit(read_from_worker, 2)
                barrier.wait()
                worker_one_connection_id, worker_one_count = future_one.result()
                worker_two_connection_id, worker_two_count = future_two.result()

            self.assertEqual(worker_one_count, 1)
            self.assertEqual(worker_two_count, 1)
            self.assertNotEqual(worker_one_connection_id, main_thread_connection_id)
            self.assertNotEqual(worker_two_connection_id, main_thread_connection_id)
        finally:
            connection.close()
