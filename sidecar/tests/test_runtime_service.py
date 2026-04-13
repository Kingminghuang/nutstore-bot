from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from smolagents.models import (
    ChatMessage,
    ChatMessageStreamDelta,
    MessageRole,
    Model,
)
from smolagents.monitoring import TokenUsage

from nsbot_sidecar.runtime.memory import MemoryConsolidator
from nsbot_sidecar.providers.direct_model import DirectModelConfig
from nsbot_sidecar.runtime.native_code_agent import NativeCodeAgent
from nsbot_sidecar.providers.direct_model import DirectModelError
from nsbot_sidecar.runtime.engine import SmolagentsRuntimeEngine
from nsbot_sidecar.runtime.runtime_service import (
    RunMetadata,
    RuntimeProcessError,
    RuntimeWorkerConfig,
)
from nsbot_sidecar.application.run_service import execute_runtime_run


class FakeStreamingModel(Model):
    def __init__(self, answer: str):
        super().__init__(model_id="fake")
        self.answer = answer

    def generate_stream(
        self,
        messages,
        stop_sequences=None,
        response_format=None,
        tools_to_call_from=None,
        **kwargs,
    ):
        payload = (
            f"Thought: solve quickly\n<code>\nfinal_answer('{self.answer}')\n</code>"
        )
        yield ChatMessageStreamDelta(
            content=payload,
            token_usage=TokenUsage(input_tokens=10, output_tokens=12),
        )

    def generate(
        self,
        messages,
        stop_sequences=None,
        response_format=None,
        tools_to_call_from=None,
        **kwargs,
    ):
        return ChatMessage(role=MessageRole.ASSISTANT, content="unused")


class FakeDirectFailureModel(Model):
    def __init__(self):
        super().__init__(model_id="fake")

    def generate_stream(
        self,
        messages,
        stop_sequences=None,
        response_format=None,
        tools_to_call_from=None,
        **kwargs,
    ):
        raise DirectModelError("provider_timeout", "provider timed out")

    def generate(
        self,
        messages,
        stop_sequences=None,
        response_format=None,
        tools_to_call_from=None,
        **kwargs,
    ):
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
        service = SmolagentsRuntimeEngine(
            self._config(),
            model_factory=lambda: FakeStreamingModel("ok"),
        )

        result = service.process(
            run_id="run-1",
            user_input="say ok",
            auth_context={},
            metadata=RunMetadata(
                workspace_path=str(self.workspace_a), session_key=None
            ),
        )

        self.assertEqual(result["final_answer"], "ok")
        self.assertGreaterEqual(len(result["deltas"]), 1)
        self.assertEqual(result["timeline_entries"][0]["entry_kind"], "action")
        payload = json.loads(result["timeline_entries"][0]["content_json"])
        self.assertEqual(payload["thought"], "solve quickly")
        self.assertGreaterEqual(len(payload["observations"]), 1)
        self.assertEqual(payload["usage"]["reasoningTokens"], 0)

    def test_direct_mode_execution(self) -> None:
        cfg = replace(
            self._config(),
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4.1",
        )

        service = SmolagentsRuntimeEngine(
            cfg,
            model_factory=lambda: FakeStreamingModel("direct-ok"),
        )

        result = service.process(
            run_id="run-direct",
            user_input="task",
            auth_context={},
            metadata=RunMetadata(
                workspace_path=str(self.workspace_a), session_key=None
            ),
        )

        self.assertEqual(result["final_answer"], "direct-ok")

    def test_direct_mode_passes_reasoning_effort_to_model_factory(self) -> None:
        captured: dict[str, object] = {}

        cfg = replace(
            self._config(),
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4.1",
            direct_reasoning_effort="high",
        )

        def fake_model_factory(config: DirectModelConfig):
            captured["reasoning_effort"] = config.reasoning_effort
            return FakeStreamingModel("direct-ok")

        service = SmolagentsRuntimeEngine(cfg, model_factory=fake_model_factory)

        result = service.process(
            run_id="run-direct-reasoning",
            user_input="task",
            auth_context={},
            metadata=RunMetadata(
                workspace_path=str(self.workspace_a), session_key=None
            ),
        )

        self.assertEqual(result["final_answer"], "direct-ok")
        self.assertEqual(captured["reasoning_effort"], "high")

    def test_direct_mode_requires_api_key(self) -> None:
        cfg = replace(
            self._config(),
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_key="",
            model="gpt-4.1",
        )

        service = SmolagentsRuntimeEngine(cfg)

        with self.assertRaises(RuntimeProcessError) as ctx:
            service.process(
                run_id="run-direct-no-key",
                user_input="task",
                auth_context={},
                metadata=RunMetadata(
                    workspace_path=str(self.workspace_a), session_key=None
                ),
            )

        self.assertEqual(ctx.exception.code, "missing_api_key")

    def test_provider_error_passthrough(self) -> None:
        cfg = replace(
            self._config(),
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4.1",
        )

        def direct_failure_factory() -> Model:
            raise DirectModelError("provider_timeout", "provider timed out")

        service = SmolagentsRuntimeEngine(cfg, model_factory=direct_failure_factory)

        with self.assertRaises(RuntimeProcessError) as ctx:
            service.process(
                run_id="run-provider-failure",
                user_input="task",
                auth_context={},
                metadata=RunMetadata(
                    workspace_path=str(self.workspace_a), session_key=None
                ),
            )

        self.assertEqual(ctx.exception.code, "provider_timeout")

    def test_consolidation_failure_is_best_effort(self) -> None:
        service = SmolagentsRuntimeEngine(
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
            metadata=RunMetadata(
                workspace_path=str(self.workspace_a), session_key=None
            ),
        )

        self.assertEqual(result["final_answer"], "done")

    def test_workspace_session_isolation(self) -> None:
        service = SmolagentsRuntimeEngine(
            self._config(),
            model_factory=lambda: FakeStreamingModel("ok"),
        )

        service.process(
            run_id="run-a",
            user_input="task-a",
            auth_context={},
            metadata=RunMetadata(
                workspace_path=str(self.workspace_a), session_key=None
            ),
        )
        service.process(
            run_id="run-b",
            user_input="task-b",
            auth_context={},
            metadata=RunMetadata(
                workspace_path=str(self.workspace_b), session_key=None
            ),
        )

        self.assertEqual(len(service.sessions.cache), 2)
        keys = sorted(service.sessions.cache.keys())
        self.assertNotEqual(keys[0], keys[1])

    def test_windows_workspace_variants_share_default_session_key(self) -> None:
        cfg = replace(self._config(), tool_os_type="windows")
        service = SmolagentsRuntimeEngine(
            cfg,
            model_factory=lambda: FakeStreamingModel("ok"),
        )

        service.process(
            run_id="run-win-1",
            user_input="task-a",
            auth_context={},
            metadata=RunMetadata(
                workspace_path="C:/Workspace/Project", session_key=None
            ),
        )
        service.process(
            run_id="run-win-2",
            user_input="task-b",
            auth_context={},
            metadata=RunMetadata(
                workspace_path="/mnt/c/workspace/project", session_key=None
            ),
        )
        service.process(
            run_id="run-win-3",
            user_input="task-c",
            auth_context={},
            metadata=RunMetadata(
                workspace_path="/cygdrive/c/Workspace/Project", session_key=None
            ),
        )

        self.assertEqual(len(service.sessions.cache), 1)
        self.assertEqual(
            next(iter(service.sessions.cache.keys())),
            "workspace:c:\\workspace\\project",
        )

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
        self.assertIn(
            "Here are the rules you should always follow to solve your task", prompt
        )
        self.assertNotIn("Above examples were using notional tools", prompt)

    def test_execute_runtime_run_forwards_event_callback_and_is_cancelled(self) -> None:
        cfg = self._config()
        metadata = RunMetadata(
            workspace_path=str(self.workspace_a),
            session_key="session-1",
        )
        callback = lambda _event: None
        is_cancelled = lambda: False

        with patch(
            "nsbot_sidecar.application.run_service.create_runtime_engine"
        ) as engine_factory:
            runtime_engine = engine_factory.return_value
            runtime_engine.process.return_value = {
                "deltas": [],
                "timeline_entries": [],
                "final_answer": "ok",
            }

            result = execute_runtime_run(
                cfg,
                "run-1",
                "task",
                {"auth": "ctx"},
                metadata,
                event_callback=callback,
                is_cancelled=is_cancelled,
            )

        self.assertEqual(result["final_answer"], "ok")
        engine_factory.assert_called_once_with(cfg)
        runtime_engine.process.assert_called_once_with(
            run_id="run-1",
            user_input="task",
            auth_context={"auth": "ctx"},
            metadata=metadata,
            event_callback=callback,
            is_cancelled=is_cancelled,
        )


if __name__ == "__main__":
    unittest.main()
