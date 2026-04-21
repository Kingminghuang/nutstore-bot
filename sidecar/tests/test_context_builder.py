from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from nsbot.runtime.context_builder import ContextBuilder, ContextBuilderConfig, ContextBuildError, RuntimeInfo
from nsbot.runtime.memory import MemoryStore


class ContextBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="ctx-builder-"))
        (self.temp_dir / "templates").mkdir(parents=True, exist_ok=True)
        (self.temp_dir / "memory").mkdir(parents=True, exist_ok=True)

        (self.temp_dir / "templates" / "IDENTITFY.md").write_text(
            "# ID\n{{runtime}}\n{{workspace_path}}\n{{platform_policy}}\n",
            encoding="utf-8",
        )
        (self.temp_dir / "templates" / "SOUL.md").write_text("soul", encoding="utf-8")
        (self.temp_dir / "templates" / "USER.md").write_text("user", encoding="utf-8")
        (self.temp_dir / "templates" / "TOOLS.md").write_text("tools", encoding="utf-8")
        (self.temp_dir / "memory" / "MEMORY.md").write_text("remember me", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _runtime_info(self) -> RuntimeInfo:
        return RuntimeInfo(os_name="Linux", arch="x86_64", python_version="3.13.7")

    def test_builds_all_layers_in_order(self) -> None:
        builder = ContextBuilder()
        memory_store = MemoryStore(str(self.temp_dir))
        config = ContextBuilderConfig(ns_bot_home=str(self.temp_dir), workspace_path="/tmp/ws")

        out = builder.build_system_prompt(config, self._runtime_info(), memory_store)

        self.assertIn("# ID", out)
        self.assertNotIn("{{runtime}}", out)
        self.assertNotIn("{{workspace_path}}", out)
        self.assertNotIn("{{platform_policy}}", out)
        self.assertTrue(out.index("# ID") < out.index("## SOUL.md"))
        self.assertTrue(out.index("## SOUL.md") < out.index("## USER.md"))
        self.assertTrue(out.index("## USER.md") < out.index("## TOOLS.md"))
        self.assertTrue(out.index("## TOOLS.md") < out.index("# Memory"))
        self.assertIn("\n\n---\n\n", out)

    def test_skips_missing_bootstrap_files(self) -> None:
        (self.temp_dir / "templates" / "TOOLS.md").unlink(missing_ok=True)
        builder = ContextBuilder()
        memory_store = MemoryStore(str(self.temp_dir))
        config = ContextBuilderConfig(ns_bot_home=str(self.temp_dir), workspace_path="/tmp/ws")

        out = builder.build_system_prompt(config, self._runtime_info(), memory_store)

        self.assertIn("## SOUL.md", out)
        self.assertIn("## USER.md", out)
        self.assertNotIn("## TOOLS.md", out)

    def test_identity_template_missing_raises(self) -> None:
        (self.temp_dir / "templates" / "IDENTITFY.md").unlink(missing_ok=True)
        builder = ContextBuilder()
        memory_store = MemoryStore(str(self.temp_dir))
        config = ContextBuilderConfig(ns_bot_home=str(self.temp_dir), workspace_path="/tmp/ws")

        with self.assertRaises(ContextBuildError):
            builder.build_system_prompt(config, self._runtime_info(), memory_store)

    def test_identity_known_placeholders_all_replaced_even_if_repeated(self) -> None:
        (self.temp_dir / "templates" / "IDENTITFY.md").write_text(
            "# ID\n{{runtime}}\n{{workspace_path}}\n{{platform_policy}}\n{{runtime}}\n",
            encoding="utf-8",
        )
        builder = ContextBuilder()
        memory_store = MemoryStore(str(self.temp_dir))
        config = ContextBuilderConfig(ns_bot_home=str(self.temp_dir), workspace_path="/tmp/ws")

        # Rendering should still succeed because known placeholders are repeated and fully replaced.
        out = builder.build_system_prompt(config, self._runtime_info(), memory_store)
        self.assertNotIn("{{runtime}}", out)

    def test_deterministic_output(self) -> None:
        builder = ContextBuilder()
        memory_store = MemoryStore(str(self.temp_dir))
        config = ContextBuilderConfig(ns_bot_home=str(self.temp_dir), workspace_path="/tmp/ws")

        first = builder.build_system_prompt(config, self._runtime_info(), memory_store)
        second = builder.build_system_prompt(config, self._runtime_info(), memory_store)

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
