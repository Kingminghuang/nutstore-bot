from __future__ import annotations

import json
import unittest

from python_runtime.worker import parse_request


class WorkerRequestParsingTests(unittest.TestCase):
    def test_parse_request_accepts_snake_case(self) -> None:
        raw = json.dumps(
            {
                "run_id": "run-1",
                "user_input": "hello",
                "auth_context": {"gateway_token": "token-1"},
                "metadata": {"workspace_path": "/tmp/ws", "session_key": "s1"},
                "config": {
                    "gateway_base_url": "http://127.0.0.1:18000",
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
        self.assertEqual(req.auth_context["gateway_token"], "token-1")
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
                "authContext": {"gatewayToken": "token-2", "expEpoch": 123},
                "metadata": {"workspacePath": "/tmp/ws2", "sessionKey": "s2"},
                "config": {
                    "gatewayBaseUrl": "http://127.0.0.1:18000",
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
        self.assertEqual(req.auth_context["gateway_token"], "token-2")
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
                    "gatewayBaseUrl": "http://127.0.0.1:18000",
                    "modelId": "gpt-5.4",
                    "runtimeMode": "direct",
                    "directProvider": "openai",
                    "directBaseUrl": "https://api.openai.com/v1",
                    "directApiKey": "sk-test",
                    "directModelId": "gpt-4.1",
                    "directRequestTimeoutMs": 45000,
                    "nsBotHome": "/tmp/.nsbot",
                    "workspacePathDefault": "/tmp",
                    "maxSteps": 7,
                },
            }
        )

        req = parse_request(raw)
        self.assertEqual(req.config.runtime_mode, "direct")
        self.assertEqual(req.config.direct_provider, "openai")
        self.assertEqual(req.config.direct_base_url, "https://api.openai.com/v1")
        self.assertEqual(req.config.direct_api_key, "sk-test")
        self.assertEqual(req.config.direct_model_id, "gpt-4.1")
        self.assertEqual(req.config.direct_request_timeout_ms, 45000)


if __name__ == "__main__":
    unittest.main()
