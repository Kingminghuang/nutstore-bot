from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nsbot_sidecar.infrastructure.local_paths import (
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

    def test_windows_prefers_appdata(self) -> None:
        with patch("nsbot_sidecar.infrastructure.local_paths.sys.platform", "win32"):
            with patch.dict(
                os.environ,
                {"APPDATA": r"C:\\Users\\test\\AppData\\Roaming"},
                clear=True,
            ):
                self.assertEqual(
                    nsbot_home(),
                    (Path(r"C:\Users\test\AppData\Roaming") / "NutstoreBot").resolve(),
                )

    def test_linux_prefers_xdg_state_home(self) -> None:
        with patch("nsbot_sidecar.infrastructure.local_paths.sys.platform", "linux"):
            with patch.dict(
                os.environ, {"XDG_STATE_HOME": "/tmp/xdg-state"}, clear=True
            ):
                self.assertEqual(
                    nsbot_home(),
                    (Path("/tmp/xdg-state") / "NutstoreBot").resolve(),
                )
