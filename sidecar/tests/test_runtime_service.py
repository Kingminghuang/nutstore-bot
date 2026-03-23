from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from smolagents.models import ChatMessage, ChatMessageStreamDelta, MessageRole, Model
from smolagents.monitoring import TokenUsage

from python_runtime.memory import MemoryConsolidator
from python_runtime.native_code_agent import NativeCodeAgent
from python_runtime.direct_model import DirectModelError
from python_runtime.runtime_service import CodeAgentRuntimeService, RunMetadata, RuntimeProcessError, RuntimeWorkerConfig


class FakeStreamingModel(Model):
    def __init__(self, answer: str):
        super().__init__(model_id="fake")
        self.answer = answer

    def generate_stream(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kwargs):
        payload = (
            "Thought: solve quickly\n"
            "<code>\n"
            f"final_answer('{self.answer}')\n"
            "</code>"
        )
        yield ChatMessageStreamDelta(
            content=payload,
            token_usage=TokenUsage(input_tokens=10, output_tokens=12),
        )

    def generate(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kwargs):
        return ChatMessage(role=MessageRole.ASSISTANT, content="unused")





class FakeDirectFailureModel(Model):
    def __init__(self):
        super().__init__(model_id="fake")

    def generate_stream(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kwargs):
        raise DirectModelError("provider_timeout", "provider timed out")

    def generate(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kwargs):
        raise DirectModelError("provider_timeout", "provider timed out")


class RuntimeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="runtime-service-"))
        (self.temp_dir / "templates").mkdir(parents=True, exist_ok=True)
        (self.temp_dir / "memory").mkdir(parents=True, exist_ok=True)
        (self.temp_dir / "templates" / "IDENTITFY.md").write_text(
            "# identity\n{{runtime}}\n{{workspace_path}}\n{{platform_policy}}\n",
            encoding="utf-8",
        )
        (self.temp_dir / "templates" / "SOUL.md").write_text("soul", encoding="utf-8")
        (self.temp_dir / "templates" / "USER.md").write_text("user", encoding="utf-8")
        (self.temp_dir / "templates" / "TOOLS.md").write_text("tools", encoding="utf-8")

        self.workspace_a = self.temp_dir / "ws-a"
        self.workspace_b = self.temp_dir / "ws-b"
        self.workspace_a.mkdir(parents=True, exist_ok=True)
        self.workspace_b.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _config(self) -> RuntimeWorkerConfig:
        return RuntimeWorkerConfig(
            model_id="gpt-5.4",
            ns_bot_home=str(self.temp_dir),
            workspace_path_default=str(self.workspace_a),
            max_steps=6,
        )

    def test_has_delta_and_step_normalization(self) -> None:
        service = CodeAgentRuntimeService(
            self._config(),
            model_factory=lambda: FakeStreamingModel("ok"),
        )

        result = service.process(
            run_id="run-1",
            user_input="say ok",
            auth_context={},
            metadata=RunMetadata(workspace_path=str(self.workspace_a), session_key=None),
        )

        self.assertEqual(result["final_answer"], "ok")
        self.assertGreaterEqual(len(result["deltas"]), 1)
        self.assertEqual(result["steps"][0]["step_kind"], "action")
        self.assertTrue(result["steps"][0]["has_delta"])
        self.assertEqual(result["steps"][0]["usage"]["reasoning_tokens"], 0)

    def test_direct_mode_execution(self) -> None:
        cfg = replace(
            self._config(),
            direct_provider="openai",
            direct_base_url="https://api.openai.com/v1",
            direct_api_key="sk-test",
            direct_model_id="gpt-4.1",
        )

        service = CodeAgentRuntimeService(
            cfg,
            model_factory=lambda: FakeStreamingModel("direct-ok"),
        )

        result = service.process(
            run_id="run-direct",
            user_input="task",
            auth_context={},
            metadata=RunMetadata(workspace_path=str(self.workspace_a), session_key=None),
        )

        self.assertEqual(result["final_answer"], "direct-ok")

    def test_direct_mode_requires_api_key(self) -> None:
        cfg = replace(
            self._config(),
            direct_provider="openai",
            direct_base_url="https://api.openai.com/v1",
            direct_api_key="",
            direct_model_id="gpt-4.1",
        )

        service = CodeAgentRuntimeService(cfg)

        with self.assertRaises(RuntimeProcessError) as ctx:
            service.process(
                run_id="run-direct-no-key",
                user_input="task",
                auth_context={},
                metadata=RunMetadata(workspace_path=str(self.workspace_a), session_key=None),
            )

        self.assertEqual(ctx.exception.code, "missing_api_key")

    def test_direct_provider_error_passthrough(self) -> None:
        cfg = replace(
            self._config(),
            direct_provider="openai",
            direct_base_url="https://api.openai.com/v1",
            direct_api_key="sk-test",
            direct_model_id="gpt-4.1",
        )

        def direct_failure_factory() -> Model:
            raise DirectModelError("provider_timeout", "provider timed out")

        service = CodeAgentRuntimeService(cfg, model_factory=direct_failure_factory)

        with self.assertRaises(RuntimeProcessError) as ctx:
            service.process(
                run_id="run-direct-provider-failure",
                user_input="task",
                auth_context={},
                metadata=RunMetadata(workspace_path=str(self.workspace_a), session_key=None),
            )

        self.assertEqual(ctx.exception.code, "provider_timeout")

    def test_consolidation_failure_is_best_effort(self) -> None:
        service = CodeAgentRuntimeService(
            self._config(),
            model_factory=lambda: FakeStreamingModel("done"),
            consolidator_factory=lambda sessions, store: MemoryConsolidator(
                sessions,
                store,
                fail_on_call=True,
            ),
        )

        result = service.process(
            run_id="run-3",
            user_input="continue",
            auth_context={},
            metadata=RunMetadata(workspace_path=str(self.workspace_a), session_key=None),
        )

        self.assertEqual(result["final_answer"], "done")

    def test_workspace_session_isolation(self) -> None:
        service = CodeAgentRuntimeService(
            self._config(),
            model_factory=lambda: FakeStreamingModel("ok"),
        )

        service.process(
            run_id="run-a",
            user_input="task-a",
            auth_context={},
            metadata=RunMetadata(workspace_path=str(self.workspace_a), session_key=None),
        )
        service.process(
            run_id="run-b",
            user_input="task-b",
            auth_context={},
            metadata=RunMetadata(workspace_path=str(self.workspace_b), session_key=None),
        )

        self.assertEqual(len(service.sessions.cache), 2)
        keys = sorted(service.sessions.cache.keys())
        self.assertNotEqual(keys[0], keys[1])

    def test_windows_workspace_variants_share_default_session_key(self) -> None:
        cfg = replace(self._config(), tool_os_type="windows")
        service = CodeAgentRuntimeService(
            cfg,
            model_factory=lambda: FakeStreamingModel("ok"),
        )

        service.process(
            run_id="run-win-1",
            user_input="task-a",
            auth_context={},
            metadata=RunMetadata(workspace_path="C:/Workspace/Project", session_key=None),
        )
        service.process(
            run_id="run-win-2",
            user_input="task-b",
            auth_context={},
            metadata=RunMetadata(workspace_path="/mnt/c/workspace/project", session_key=None),
        )
        service.process(
            run_id="run-win-3",
            user_input="task-c",
            auth_context={},
            metadata=RunMetadata(workspace_path="/cygdrive/c/Workspace/Project", session_key=None),
        )

        self.assertEqual(len(service.sessions.cache), 1)
        self.assertEqual(next(iter(service.sessions.cache.keys())), "workspace:c:\\workspace\\project")

    def test_native_code_agent_system_prompt_uses_context_prefix(self) -> None:
        context_prefix = "# Identity\n\n## TOOLS.md\nls/read/write"
        agent = NativeCodeAgent(
            tools=[],
            model=FakeStreamingModel("ok"),
            context_prefix=context_prefix,
            max_steps=2,
        )

        prompt = agent.system_prompt
        self.assertIn("# Identity", prompt)
        self.assertIn("## TOOLS.md", prompt)
        self.assertIn("Thought:", prompt)
        self.assertIn("final_answer", prompt)
        self.assertIn("Here are the rules you should always follow to solve your task", prompt)
        self.assertNotIn("Above examples were using notional tools", prompt)


if __name__ == "__main__":
    unittest.main()
