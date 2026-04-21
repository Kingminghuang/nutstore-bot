from __future__ import annotations

import json
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from nsbot.runtime.tools import (
    ToolCall,
    ToolLayer,
    build_workspace_tools,
    path_identity,
    resolve_path_arg,
    under_root,
)


class ToolLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="tool-layer-"))
        self.workspace = self.temp_dir / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "nested").mkdir(parents=True, exist_ok=True)
        (self.workspace / "nested" / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_script(self, name: str, body: str) -> str:
        target = self.temp_dir / name
        target.write_text(body, encoding="utf-8")
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
        return str(target)

    def test_unknown_tool_returns_invalid_args(self) -> None:
        layer = ToolLayer(str(self.workspace))
        result = layer.execute_tool(ToolCall(tool_name="http_request", args={}, call_id="1"))
        self.assertTrue(result.is_error)
        assert result.error is not None
        self.assertEqual(result.error.code, "invalid_args")

    def test_workspace_escape_denied(self) -> None:
        layer = ToolLayer(str(self.workspace))
        result = layer.execute_tool_dict("read", {"path": "../outside.txt"})
        self.assertTrue(result["is_error"])
        self.assertEqual(result["error"]["code"], "permission_denied")

    def test_ls_empty_directory_message(self) -> None:
        (self.workspace / "empty").mkdir(parents=True, exist_ok=True)
        layer = ToolLayer(str(self.workspace))
        result = layer.execute_tool_dict("ls", {"path": "empty"})
        self.assertFalse(result["is_error"])
        self.assertEqual(result["content"][0]["text"], "(empty directory)")

    def test_ls_skips_hidden_entries(self) -> None:
        (self.workspace / "visible.txt").write_text("visible\n", encoding="utf-8")
        (self.workspace / ".hidden.txt").write_text("hidden\n", encoding="utf-8")
        (self.workspace / "visible-dir").mkdir(parents=True, exist_ok=True)
        (self.workspace / ".hidden-dir").mkdir(parents=True, exist_ok=True)
        layer = ToolLayer(str(self.workspace))

        result = layer.execute_tool_dict("ls", {"path": "."})

        self.assertFalse(result["is_error"])
        text = result["content"][0]["text"]
        self.assertIn("visible.txt", text)
        self.assertIn("visible-dir/", text)
        self.assertNotIn(".hidden.txt", text)
        self.assertNotIn(".hidden-dir/", text)

    def test_ls_only_hidden_entries_reports_empty_directory(self) -> None:
        (self.workspace / "hidden-only").mkdir(parents=True, exist_ok=True)
        (self.workspace / "hidden-only" / ".secret").write_text("secret\n", encoding="utf-8")
        (self.workspace / "hidden-only" / ".config").mkdir(parents=True, exist_ok=True)
        layer = ToolLayer(str(self.workspace))

        result = layer.execute_tool_dict("ls", {"path": "hidden-only"})

        self.assertFalse(result["is_error"])
        self.assertEqual(result["content"][0]["text"], "(empty directory)")

    def test_write_and_read_with_continue_offset(self) -> None:
        layer = ToolLayer(str(self.workspace))
        wrote = layer.execute_tool_dict("write", {"path": "log.txt", "content": "l1\nl2\nl3\nl4\n"})
        self.assertFalse(wrote["is_error"])
        read = layer.execute_tool_dict("read", {"path": "log.txt", "offset": 1, "limit": 2})
        self.assertFalse(read["is_error"])
        self.assertIn("Use offset=3 to continue", read["content"][0]["text"])

    def test_find_uses_fd_and_reports_result_limit(self) -> None:
        fd_script = self._make_script(
            "fake-fd.sh",
            "#!/bin/sh\nprintf 'one.txt\\ntwo.txt\\nthree.txt\\n'\n",
        )
        layer = ToolLayer(str(self.workspace), fd_executable=fd_script)
        result = layer.execute_tool_dict("find", {"pattern": "*.txt", "path": ".", "limit": 2})
        self.assertFalse(result["is_error"])
        self.assertEqual(result["details"]["resultLimitReached"], 2)
        self.assertIn("2 results limit reached.", result["content"][0]["text"])

    def test_find_does_not_pass_hidden_flag_to_fd(self) -> None:
        capture_file = self.temp_dir / "fd-args.txt"
        fd_script = self._make_script(
            "fake-fd-args.sh",
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > '{capture_file}'\n"
            "printf 'visible.txt\\n'\n",
        )
        layer = ToolLayer(str(self.workspace), fd_executable=fd_script)

        result = layer.execute_tool_dict("find", {"pattern": "*.txt", "path": "."})

        self.assertFalse(result["is_error"])
        args_text = capture_file.read_text(encoding="utf-8")
        self.assertNotIn("--hidden", args_text)

    def test_find_result_limit_applies_after_hidden_entries_are_excluded(self) -> None:
        fd_script = self._make_script(
            "fake-fd-filtered.sh",
            "#!/bin/sh\nprintf 'one.txt\\ntwo.txt\\nthree.txt\\n'\n",
        )
        layer = ToolLayer(str(self.workspace), fd_executable=fd_script)

        result = layer.execute_tool_dict("find", {"pattern": "*.txt", "path": ".", "limit": 2})

        self.assertFalse(result["is_error"])
        self.assertEqual(result["details"]["resultLimitReached"], 2)
        self.assertEqual(result["content"][0]["text"], "one.txt\ntwo.txt\n[2 results limit reached.]")

    def test_grep_line_truncation_and_match_limit(self) -> None:
        long_line = "x" * 700
        event_1 = {
            "type": "match",
            "data": {
                "path": {"text": "nested/a.txt"},
                "line_number": 1,
                "lines": {"text": long_line + "\n"},
            },
        }
        event_2 = {
            "type": "match",
            "data": {
                "path": {"text": "nested/a.txt"},
                "line_number": 2,
                "lines": {"text": "second\n"},
            },
        }
        rg_script = self._make_script(
            "fake-rg.sh",
            "#!/bin/sh\n"
            f"printf '%s\\n' '{json.dumps(event_1)}'\n"
            f"printf '%s\\n' '{json.dumps(event_2)}'\n",
        )
        layer = ToolLayer(str(self.workspace), rg_executable=rg_script)
        result = layer.execute_tool_dict(
            "grep",
            {
                "pattern": "x",
                "path": ".",
                "limit": 1,
            },
        )
        self.assertFalse(result["is_error"])
        self.assertEqual(result["details"]["matchLimitReached"], 1)
        self.assertTrue(result["details"]["linesTruncated"])
        self.assertIn("[truncated]", result["content"][0]["text"])

    def test_grep_uses_sidecar_preprocessor_and_globs(self) -> None:
        capture_file = self.temp_dir / "rg-args.txt"
        rg_script = self._make_script(
            "fake-rg-args.sh",
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > '{capture_file}'\n"
            f"printf 'RG_SIDECAR_ROOT=%s\\n' \"$RG_SIDECAR_ROOT\" >> '{capture_file}'\n"
            "exit 0\n",
        )
        layer = ToolLayer(str(self.workspace), rg_executable=rg_script)
        result = layer.execute_tool_dict("grep", {"pattern": "alpha", "path": "."})
        self.assertFalse(result["is_error"])
        args_text = capture_file.read_text(encoding="utf-8")
        self.assertIn("--pre", args_text)
        self.assertIn("--pre-glob", args_text)
        self.assertIn("*.pdf", args_text)
        self.assertIn("*.docx", args_text)
        self.assertIn("*.xlsx", args_text)
        self.assertIn("RG_SIDECAR_ROOT=", args_text)

    def test_grep_preserves_original_binary_file_path_in_output(self) -> None:
        event = {
            "type": "match",
            "data": {
                "path": {"text": "nested/report.pdf"},
                "line_number": 12,
                "lines": {"text": "budget increased by 20%\\n"},
            },
        }
        rg_script = self._make_script(
            "fake-rg-binary-path.sh",
            "#!/bin/sh\n"
            f"printf '%s\\n' '{json.dumps(event)}'\n",
        )
        layer = ToolLayer(str(self.workspace), rg_executable=rg_script)
        result = layer.execute_tool_dict("grep", {"pattern": "budget", "path": "."})
        self.assertFalse(result["is_error"])
        self.assertIn("nested/report.pdf:12:", result["content"][0]["text"])

    def test_grep_context_reads_sidecar_for_binary_source(self) -> None:
        (self.workspace / "nested" / "report.pdf").write_bytes(b"%PDF-1.7")
        sidecar_path = self.workspace / ".sidecar" / "nested" / "report.pdf.md"
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text("line1\nline2\nline3\n", encoding="utf-8")

        event = {
            "type": "match",
            "data": {
                "path": {"text": "nested/report.pdf"},
                "line_number": 2,
                "lines": {"text": "line2\\n"},
            },
        }
        rg_script = self._make_script(
            "fake-rg-context.sh",
            "#!/bin/sh\n"
            f"printf '%s\\n' '{json.dumps(event)}'\n",
        )
        layer = ToolLayer(str(self.workspace), rg_executable=rg_script)
        result = layer.execute_tool_dict(
            "grep",
            {"pattern": "line2", "path": ".", "context": 1},
        )
        self.assertFalse(result["is_error"])
        text = result["content"][0]["text"]
        self.assertIn("nested/report.pdf-1- line1", text)
        self.assertIn("nested/report.pdf:2: line2", text)
        self.assertIn("nested/report.pdf-3- line3", text)

    def test_grep_context_falls_back_when_binary_sidecar_missing(self) -> None:
        event = {
            "type": "match",
            "data": {
                "path": {"text": "nested/missing.pdf"},
                "line_number": 9,
                "lines": {"text": "fallback line\\n"},
            },
        }
        rg_script = self._make_script(
            "fake-rg-missing-sidecar.sh",
            "#!/bin/sh\n"
            f"printf '%s\\n' '{json.dumps(event)}'\n",
        )
        layer = ToolLayer(str(self.workspace), rg_executable=rg_script)
        result = layer.execute_tool_dict(
            "grep",
            {"pattern": "fallback", "path": ".", "context": 1},
        )
        self.assertFalse(result["is_error"])
        self.assertIn("nested/missing.pdf:9: fallback line", result["content"][0]["text"])

    def test_read_image_returns_text_and_image_payload(self) -> None:
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x17\x38U"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        (self.workspace / "tiny.png").write_bytes(png_bytes)
        layer = ToolLayer(str(self.workspace))
        result = layer.execute_tool_dict("read", {"path": "tiny.png"})
        self.assertFalse(result["is_error"])
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(result["content"][1]["type"], "image")
        self.assertEqual(result["content"][1]["mime_type"], "image/png")

    def test_edit_returns_diff_and_first_changed_line(self) -> None:
        path = self.workspace / "edit.txt"
        path.write_text("line1\nline2\nline3\n", encoding="utf-8")
        layer = ToolLayer(str(self.workspace))
        result = layer.execute_tool_dict(
            "edit",
            {
                "path": "edit.txt",
                "old_text": "line2",
                "new_text": "line2-updated",
            },
        )
        self.assertFalse(result["is_error"])
        self.assertIn("Successfully replaced text", result["content"][0]["text"])
        self.assertEqual(result["details"]["firstChangedLine"], 2)
        self.assertIn("-line2", result["details"]["diff"])
        self.assertIn("+line2-updated", result["details"]["diff"])

    def test_write_and_edit_request_permission_when_auto_allow_disabled(self) -> None:
        captured: list[dict[str, str]] = []

        def requester(payload: dict[str, str]) -> str:
            captured.append(payload)
            return "allow"

        layer = ToolLayer(
            str(self.workspace),
            permission_requester=requester,
            auto_allow=False,
        )
        write_result = layer.execute_tool_dict(
            "write",
            {"path": "permission.txt", "content": "hello"},
        )
        edit_result = layer.execute_tool_dict(
            "edit",
            {"path": "permission.txt", "old_text": "hello", "new_text": "world"},
        )

        self.assertFalse(write_result["is_error"])
        self.assertFalse(edit_result["is_error"])
        self.assertEqual([item["kind"] for item in captured], ["write", "edit"])

    def test_write_details_report_add_for_new_file(self) -> None:
        layer = ToolLayer(str(self.workspace))
        result = layer.execute_tool_dict(
            "write",
            {"path": "new-file.txt", "content": "hello"},
        )

        self.assertFalse(result["is_error"])
        self.assertEqual(result["details"]["mutationKind"], "add")

    def test_write_details_report_update_for_existing_file(self) -> None:
        (self.workspace / "existing.txt").write_text("before", encoding="utf-8")
        layer = ToolLayer(str(self.workspace))
        result = layer.execute_tool_dict(
            "write",
            {"path": "existing.txt", "content": "after"},
        )

        self.assertFalse(result["is_error"])
        self.assertEqual(result["details"]["mutationKind"], "update")

    def test_read_tool_does_not_request_permission(self) -> None:
        requested = False

        def requester(_payload: dict[str, str]) -> str:
            nonlocal requested
            requested = True
            return "allow"

        layer = ToolLayer(
            str(self.workspace),
            permission_requester=requester,
            auto_allow=False,
        )
        result = layer.execute_tool_dict("read", {"path": "nested/a.txt"})
        self.assertFalse(result["is_error"])
        self.assertFalse(requested)

    def test_permission_reject_blocks_controlled_write(self) -> None:
        layer = ToolLayer(
            str(self.workspace),
            permission_requester=lambda _payload: "reject",
            auto_allow=False,
        )
        result = layer.execute_tool_dict(
            "write",
            {"path": "deny.txt", "content": "blocked"},
        )
        self.assertTrue(result["is_error"])
        self.assertEqual(result["error"]["code"], "permission_denied")

    def test_windows_path_normalization_helpers(self) -> None:
        p1 = resolve_path_arg("C:\\workspace\\src\\..\\README.md", "C:\\workspace", "windows")
        p2 = resolve_path_arg("C:/workspace/README.md", "C:\\workspace", "windows")
        p3 = resolve_path_arg("/c/workspace/README.md", "C:\\workspace", "windows")
        p4 = resolve_path_arg("/cygdrive/c/workspace/README.md", "C:\\workspace", "windows")
        p5 = resolve_path_arg("/mnt/c/workspace/README.md", "C:\\workspace", "windows")
        self.assertEqual(p1, "C:\\workspace\\README.md")
        self.assertEqual(p1, p2)
        self.assertEqual(p1, p3)
        self.assertEqual(p1, p4)
        self.assertEqual(p1, p5)
        self.assertEqual(path_identity("C:\\Workspace\\A", "windows"), path_identity("c:\\workspace\\a", "windows"))
        self.assertTrue(under_root("C:\\Workspace\\A", "c:\\workspace", "windows"))

    def test_workspace_tool_metadata_is_unambiguous_and_actionable(self) -> None:
        tools = build_workspace_tools(str(self.workspace))
        by_name = {tool.name: tool for tool in tools}

        self.assertIn("find", by_name)
        self.assertIn("grep", by_name)
        self.assertIn("read", by_name)
        self.assertIn("edit", by_name)

        find_inputs = by_name["find"].inputs
        self.assertEqual(sorted(find_inputs.keys()), ["limit", "path", "pattern"])
        self.assertIn("Defaults to '.'", str(find_inputs["path"]["description"]))

        grep_inputs = by_name["grep"].inputs
        self.assertIn("literal=true", str(grep_inputs["pattern"]["description"]))
        self.assertIn("default 100", str(grep_inputs["limit"]["description"]))

        read_inputs = by_name["read"].inputs
        self.assertIn("1-based", str(read_inputs["offset"]["description"]))

        edit_inputs = by_name["edit"].inputs
        self.assertIn("exactly once", str(edit_inputs["old_text"]["description"]))


if __name__ == "__main__":
    unittest.main()
