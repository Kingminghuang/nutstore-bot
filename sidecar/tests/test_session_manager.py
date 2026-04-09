from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from nsbot_sidecar.runtime.session_manager import Session, SessionManager, safe_session_key


class SessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="session-manager-"))
        self.manager = SessionManager(str(self.temp_dir))
        self.manager.legacy_sessions_dir = self.temp_dir / "legacy-sessions"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_safe_session_key_normalizes_filename(self) -> None:
        self.assertEqual(safe_session_key("  cli:bad/name?*  "), "cli_bad_name__")
        self.assertEqual(safe_session_key(""), "default")

    def test_save_and_load_preserve_metadata_messages_and_watermark(self) -> None:
        session = self.manager.get_or_create("cli:default")
        session.metadata = {"workspace_path": "/tmp/ws"}
        session.add_message("user", "你好")
        session.add_message(
            "assistant",
            "done",
            tool_calls=[{"function": {"name": "save_memory"}}],
            name="assistant-1",
        )
        session.last_consolidated = 1

        self.manager.save(session)
        self.manager.invalidate("cli:default")

        reloaded = self.manager.get_or_create("cli:default")
        self.assertEqual(reloaded.metadata["workspace_path"], "/tmp/ws")
        self.assertEqual(reloaded.messages[0]["content"], "你好")
        self.assertEqual(reloaded.messages[1]["tool_calls"][0]["function"]["name"], "save_memory")
        self.assertEqual(reloaded.last_consolidated, 1)

        path = self.manager.session_path("cli:default")
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(json.loads(first_line)["_type"], "metadata")

    def test_get_history_respects_watermark_user_alignment_and_passthrough(self) -> None:
        session = Session(
            key="cli:history",
            messages=[
                {"role": "assistant", "content": "old"},
                {"role": "tool", "content": "tool-out", "tool_call_id": "tool-1"},
                {"role": "user", "content": "new task", "name": "alice"},
                {"role": "assistant", "content": "answer", "tool_calls": [{"name": "ls"}]},
            ],
            last_consolidated=1,
        )

        history = session.get_history(max_messages=500)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["name"], "alice")
        self.assertEqual(history[1]["tool_calls"][0]["name"], "ls")

    def test_clear_resets_messages_and_watermark_only(self) -> None:
        session = self.manager.get_or_create("cli:clear")
        original_created_at = session.created_at
        session.metadata = {"workspace_path": "/tmp/ws"}
        session.add_message("user", "hi")
        session.last_consolidated = 1

        session.clear()

        self.assertEqual(session.messages, [])
        self.assertEqual(session.last_consolidated, 0)
        self.assertEqual(session.metadata, {"workspace_path": "/tmp/ws"})
        self.assertEqual(session.created_at, original_created_at)

    def test_load_uses_last_metadata_line_when_file_contains_multiple_metadata_lines(self) -> None:
        path = self.manager.session_path("cli:multi-meta")
        path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "_type": "metadata",
                            "key": "cli:multi-meta",
                            "created_at": "2026-03-10T00:00:00Z",
                            "updated_at": "2026-03-10T00:00:00Z",
                            "metadata": {"workspace_path": "/tmp/a"},
                            "last_consolidated": 1,
                        }
                    ),
                    json.dumps({"role": "user", "content": "hello"}),
                    json.dumps(
                        {
                            "_type": "metadata",
                            "key": "cli:multi-meta",
                            "created_at": "2026-03-10T00:00:00Z",
                            "updated_at": "2026-03-12T00:00:00Z",
                            "metadata": {"workspace_path": "/tmp/b"},
                            "last_consolidated": 0,
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        loaded = self.manager.load("cli:multi-meta")

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.metadata["workspace_path"], "/tmp/b")
        self.assertEqual(loaded.updated_at, "2026-03-12T00:00:00Z")
        self.assertEqual(loaded.last_consolidated, 0)

    def test_load_migrates_legacy_session_on_demand(self) -> None:
        legacy_path = self.manager.legacy_session_path("cli:legacy")
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(
            json.dumps(
                {
                    "_type": "metadata",
                    "key": "cli:legacy",
                    "created_at": "2026-03-11T00:00:00Z",
                    "updated_at": "2026-03-11T00:00:00Z",
                    "metadata": {},
                    "last_consolidated": 0,
                }
            )
            + "\n"
            + json.dumps({"role": "user", "content": "migrated"})
            + "\n",
            encoding="utf-8",
        )

        loaded = self.manager.load("cli:legacy")

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.messages[0]["content"], "migrated")
        self.assertFalse(legacy_path.exists())
        self.assertTrue(self.manager.session_path("cli:legacy").exists())

    def test_corrupt_file_returns_none_and_list_sessions_skips_invalid_entries(self) -> None:
        corrupt_path = self.manager.session_path("cli:corrupt")
        corrupt_path.write_text("{not-json\n", encoding="utf-8")

        valid = self.manager.get_or_create("cli:valid")
        valid.metadata = {"workspace_path": "/tmp/ws"}
        self.manager.save(valid)

        self.assertIsNone(self.manager.load("cli:corrupt"))

        listed = self.manager.list_sessions()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["session_key"], "cli:valid")
        self.assertEqual(listed[0]["key"], "cli:valid")
        self.assertEqual(listed[0]["metadata"]["workspace_path"], "/tmp/ws")

    def test_list_sessions_returns_desc_order_and_stable_fields(self) -> None:
        first = self.manager.get_or_create("cli:first")
        first.updated_at = "2026-03-10T00:00:00Z"
        self.manager.save(first)

        second = self.manager.get_or_create("cli:second")
        second.updated_at = "2026-03-12T00:00:00Z"
        second.metadata = {"workspace_path": "/tmp/second"}
        self.manager.save(second)

        listed = self.manager.list_sessions()

        self.assertEqual([item["session_key"] for item in listed], ["cli:second", "cli:first"])
        self.assertIn("created_at", listed[0])
        self.assertIn("path", listed[0])


if __name__ == "__main__":
    unittest.main()
