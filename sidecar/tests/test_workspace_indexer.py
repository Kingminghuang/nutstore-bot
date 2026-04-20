from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nsbot_sidecar.runtime.workspace_indexer import WorkspaceIndexer


class FakeBackgroundTasks:
    def __init__(self) -> None:
        self.calls: list[tuple[object, tuple[object, ...]]] = []

    def add_task(self, func, *args) -> None:
        self.calls.append((func, args))


class WorkspaceIndexerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="workspace-indexer-"))
        self.workspace = self.temp_dir / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.indexer = WorkspaceIndexer()

    def test_enqueue_uses_background_tasks(self) -> None:
        background_tasks = FakeBackgroundTasks()

        self.indexer.enqueue(background_tasks, "ws_1", str(self.workspace))

        self.assertEqual(len(background_tasks.calls), 1)
        _, args = background_tasks.calls[0]
        self.assertEqual(args, ("ws_1", str(self.workspace)))

    def test_index_workspace_scans_root_and_non_hidden_subdirectories(self) -> None:
        (self.workspace / "root.pdf").write_bytes(b"%PDF-1.7")
        (self.workspace / "docs" / "a").mkdir(parents=True, exist_ok=True)
        (self.workspace / "docs" / "a" / "b.docx").write_bytes(b"docx")
        (self.workspace / ".hidden").mkdir(parents=True, exist_ok=True)
        (self.workspace / ".hidden" / "skip.pdf").write_bytes(b"%PDF-1.7")
        (self.workspace / "docs" / ".private").mkdir(parents=True, exist_ok=True)
        (self.workspace / "docs" / ".private" / "skip.xlsx").write_bytes(b"xlsx")

        with patch(
            "nsbot_sidecar.runtime.workspace_indexer._convert_file",
            side_effect=lambda _source, output: output.write_text("converted", encoding="utf-8"),
        ):
            self.indexer.index_workspace("ws_1", str(self.workspace))

        self.assertTrue((self.workspace / ".sidecar" / "root.pdf.md").exists())
        self.assertTrue((self.workspace / ".sidecar" / "docs" / "a" / "b.docx.md").exists())
        self.assertFalse((self.workspace / ".sidecar" / ".hidden" / "skip.pdf.md").exists())
        self.assertFalse(
            (self.workspace / ".sidecar" / "docs" / ".private" / "skip.xlsx.csv").exists()
        )

    def test_incremental_behavior_skips_unchanged_and_reconverts_changed(self) -> None:
        source_file = self.workspace / "nested" / "report.pdf"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_bytes(b"%PDF-a")

        converted_files: list[Path] = []

        def _fake_convert(source: Path, output: Path) -> None:
            converted_files.append(source)
            output.write_text(source.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")

        with patch("nsbot_sidecar.runtime.workspace_indexer._convert_file", side_effect=_fake_convert):
            self.indexer.index_workspace("ws_1", str(self.workspace))
            self.indexer.index_workspace("ws_1", str(self.workspace))
            source_file.write_bytes(b"%PDF-b-updated")
            self.indexer.index_workspace("ws_1", str(self.workspace))

        self.assertEqual(len(converted_files), 2)
        self.assertTrue(all(path.name == "report.pdf" for path in converted_files))

    def test_deleted_source_keeps_existing_output(self) -> None:
        source_file = self.workspace / "book.xlsx"
        source_file.write_bytes(b"xlsx")

        with patch(
            "nsbot_sidecar.runtime.workspace_indexer._convert_file",
            side_effect=lambda _source, output: output.write_text("converted", encoding="utf-8"),
        ):
            self.indexer.index_workspace("ws_1", str(self.workspace))

        output_file = self.workspace / ".sidecar" / "book.xlsx.csv"
        self.assertTrue(output_file.exists())
        source_file.unlink()

        with patch(
            "nsbot_sidecar.runtime.workspace_indexer._convert_file",
            side_effect=lambda _source, output: output.write_text("converted", encoding="utf-8"),
        ):
            self.indexer.index_workspace("ws_1", str(self.workspace))

        self.assertTrue(output_file.exists())
        manifest = json.loads(
            (self.workspace / ".sidecar" / ".index-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("book.xlsx", manifest["sources"])

    def test_output_file_suffixes_keep_original_extension(self) -> None:
        (self.workspace / "a.PDF").write_bytes(b"%PDF")
        (self.workspace / "b.docx").write_bytes(b"docx")
        (self.workspace / "c.xlsx").write_bytes(b"xlsx")

        with patch(
            "nsbot_sidecar.runtime.workspace_indexer._convert_file",
            side_effect=lambda _source, output: output.write_text("converted", encoding="utf-8"),
        ):
            self.indexer.index_workspace("ws_1", str(self.workspace))

        self.assertTrue((self.workspace / ".sidecar" / "a.PDF.md").exists())
        self.assertTrue((self.workspace / ".sidecar" / "b.docx.md").exists())
        self.assertTrue((self.workspace / ".sidecar" / "c.xlsx.csv").exists())

    def test_reindexes_unchanged_file_after_refresh_window(self) -> None:
        source_file = self.workspace / "report.pdf"
        source_file.write_bytes(b"%PDF-same")

        converted_files: list[Path] = []

        def _fake_convert(source: Path, output: Path) -> None:
            converted_files.append(source)
            output.write_text("converted", encoding="utf-8")

        with patch(
            "nsbot_sidecar.runtime.workspace_indexer._convert_file",
            side_effect=_fake_convert,
        ):
            self.indexer.index_workspace("ws_1", str(self.workspace))

            manifest_path = self.workspace / ".sidecar" / ".index-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["sources"]["report.pdf"]["updatedAt"] = (
                datetime.now(timezone.utc) - timedelta(days=2)
            ).isoformat()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            self.indexer.index_workspace("ws_1", str(self.workspace))

        self.assertEqual(len(converted_files), 2)

    def test_reindex_replaces_read_only_output_atomically(self) -> None:
        source_file = self.workspace / "nested" / "report.pdf"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("version-a", encoding="utf-8")

        def _fake_convert(source: Path, output: Path) -> None:
            output.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

        with patch(
            "nsbot_sidecar.runtime.workspace_indexer._convert_file",
            side_effect=_fake_convert,
        ):
            self.indexer.index_workspace("ws_1", str(self.workspace))

            output_file = self.workspace / ".sidecar" / "nested" / "report.pdf.md"
            self.assertEqual(output_file.read_text(encoding="utf-8"), "version-a")
            self.assertEqual(stat.S_IMODE(output_file.stat().st_mode) & 0o222, 0)

            source_file.write_text("version-b-updated", encoding="utf-8")
            self.indexer.index_workspace("ws_1", str(self.workspace))

        self.assertEqual(output_file.read_text(encoding="utf-8"), "version-b-updated")
        self.assertEqual(stat.S_IMODE(output_file.stat().st_mode) & 0o222, 0)