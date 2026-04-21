from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nsbot.infrastructure.local_paths import (
    database_file_path,
    master_key_path,
    nsbot_home,
    secrets_dir_path,
)


class LocalPathsTests(unittest.TestCase):
    def test_ns_bot_home_override_has_highest_priority(self) -> None:
        custom = tempfile.mkdtemp(prefix="nsbot-home-")
        self.assertEqual(nsbot_home(custom), Path(custom).resolve())
        self.assertEqual(
            database_file_path(custom), Path(custom).resolve() / "sidecar.db"
        )
        self.assertEqual(secrets_dir_path(custom), Path(custom).resolve() / "secrets")
        self.assertEqual(master_key_path(custom), Path(custom).resolve() / "master.key")

    def test_default_home_is_always_user_home_nutstorebot(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(nsbot_home(), (Path.home() / "NutstoreBot").resolve())
