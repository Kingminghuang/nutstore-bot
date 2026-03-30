from __future__ import annotations

import shutil
import tempfile
import unittest

from python_runtime.storage import (
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
            self.assertIn("provider_connections", tables)
            self.assertIn("provider_models", tables)
            self.assertIn("provider_headers", tables)
            self.assertIn("sessions", tables)
            self.assertIn("timeline_entries", tables)
            self.assertIn("attachments", tables)
            self.assertIn("draft_attachments", tables)
            self.assertIn("runs", tables)
        finally:
            connection.close()
