from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from python_runtime.worker import parse_request


class WorkerRequestParsingTests(unittest.TestCase):
    def test_parse_request_accepts_snake_case(self) -> None:
        raw = json.dumps(
            {
                "run_id": "run-1",
                "user_input": "hello",
                "auth_context": {},
                "metadata": {"workspace_path": "/tmp/ws", "session_key": "s1"},
                "config": {
                    "model_id": "gpt-5.4",
                    "ns_bot_home": "/tmp/.nsbot",
                    "workspace_path_default": "/tmp",
                    "fd_executable": "/opt/native/fd",
                    "rg_executable": "/opt/native/rg",
                    "tool_os_type": "macos",
                    "max_steps": 9,
                },
            }
        )

        req = parse_request(raw)
        self.assertEqual(req.run_id, "run-1")
        self.assertEqual(req.user_input, "hello")
        self.assertEqual(req.metadata.workspace_path, "/tmp/ws")
        self.assertEqual(req.metadata.session_key, "s1")
        self.assertEqual(req.config.fd_executable, "/opt/native/fd")
        self.assertEqual(req.config.rg_executable, "/opt/native/rg")
        self.assertEqual(req.config.tool_os_type, "macos")
        self.assertEqual(req.config.max_steps, 9)

    def test_parse_request_accepts_camel_case(self) -> None:
        raw = json.dumps(
            {
                "runId": "run-2",
                "userInput": "world",
                "authContext": {"expEpoch": 123},
                "metadata": {"workspacePath": "/tmp/ws2", "sessionKey": "s2"},
                "config": {
                    "modelId": "gpt-5.4",
                    "nsBotHome": "/tmp/.nsbot",
                    "workspacePathDefault": "/tmp",
                    "fdExecutable": "/opt/native/fd",
                    "rgExecutable": "/opt/native/rg",
                    "toolOsType": "windows",
                    "maxSteps": 7,
                },
            }
        )

        req = parse_request(raw)
        self.assertEqual(req.run_id, "run-2")
        self.assertEqual(req.user_input, "world")
        self.assertEqual(req.auth_context["exp_epoch"], 123)
        self.assertEqual(req.metadata.workspace_path, "/tmp/ws2")
        self.assertEqual(req.metadata.session_key, "s2")
        self.assertEqual(req.config.fd_executable, "/opt/native/fd")
        self.assertEqual(req.config.rg_executable, "/opt/native/rg")
        self.assertEqual(req.config.tool_os_type, "windows")
        self.assertEqual(req.config.max_steps, 7)

    def test_parse_request_parses_direct_mode_fields(self) -> None:
        raw = json.dumps(
            {
                "runId": "run-3",
                "userInput": "world",
                "authContext": {},
                "metadata": {"workspacePath": "/tmp/ws3", "sessionKey": "s3"},
                "config": {
                    "modelId": "gpt-5.4",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "apiKey": "sk-test",
                    "model": "gpt-4.1",
                    "directRequestTimeoutMs": 45000,
                    "nsBotHome": "/tmp/.nsbot",
                    "workspacePathDefault": "/tmp",
                    "maxSteps": 7,
                },
            }
        )

        req = parse_request(raw)
        self.assertEqual(req.config.provider, "openai")
        self.assertEqual(req.config.base_url, "https://api.openai.com/v1")
        self.assertEqual(req.config.api_key, "sk-test")
        self.assertEqual(req.config.model, "gpt-4.1")
        self.assertEqual(req.config.direct_request_timeout_ms, 45000)

    def test_parse_request_uses_platform_nsbot_home_default(self) -> None:
        raw = json.dumps(
            {
                "runId": "run-4",
                "userInput": "default-home",
                "authContext": {},
                "metadata": {},
                "config": {},
            }
        )

        with patch("worker.sys.platform", "win32"):
            with patch.dict(
                os.environ,
                {"APPDATA": r"C:\\Users\\test\\AppData\\Roaming"},
                clear=True,
            ):
                req = parse_request(raw)

        self.assertEqual(
            req.config.ns_bot_home,
            str((Path(r"C:\Users\test\AppData\Roaming") / "NutstoreBot").resolve()),
        )


if __name__ == "__main__":
    unittest.main()
