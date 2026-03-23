from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ShowNativeCodeAgentSystemPromptScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="show-native-prompt-"))
        (self.temp_dir / "templates").mkdir(parents=True, exist_ok=True)
        (self.temp_dir / "memory").mkdir(parents=True, exist_ok=True)

        (self.temp_dir / "templates" / "IDENTITFY.md").write_text(
            "# Identity\n{{runtime}}\n{{workspace_path}}\n{{platform_policy}}\n",
            encoding="utf-8",
        )
        (self.temp_dir / "templates" / "SOUL.md").write_text("soul", encoding="utf-8")
        (self.temp_dir / "templates" / "USER.md").write_text("user", encoding="utf-8")
        (self.temp_dir / "templates" / "TOOLS.md").write_text("# Tool Definitions\n\n```python\npass\n```", encoding="utf-8")
        (self.temp_dir / "memory" / "MEMORY.md").write_text("remember", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_script_prints_full_native_code_agent_prompt(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        script_path = repo_root / "scripts" / "show_native_code_agent_system_prompt.py"

        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--ns-bot-home",
                str(self.temp_dir),
                "--workspace-path",
                "/tmp/ws",
                "--os-name",
                "Linux",
                "--arch",
                "x86_64",
                "--python-version",
                "3.13.7",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        out = result.stdout
        self.assertIn("# Identity", out)
        self.assertIn("Linux x86_64, Python 3.13.7", out)
        self.assertIn("## TOOLS.md", out)
        self.assertIn("Thought:", out)
        self.assertIn("final_answer", out)
        self.assertIn("Here are the rules you should always follow to solve your task", out)
        self.assertNotIn("Above examples were using notional tools", out)


if __name__ == "__main__":
    unittest.main()
