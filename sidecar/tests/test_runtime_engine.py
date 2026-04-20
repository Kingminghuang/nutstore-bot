from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
from nsbot_sidecar.runtime.engine import (
    SmolagentsRuntimeEngine,
    _collect_tool_results_by_call_id,
)
from nsbot_sidecar.runtime.types import (
    RunMetadata,
    RuntimeProcessError,
    RuntimeWorkerConfig,
)
from nsbot_sidecar.application.turn_service import execute_runtime_turn


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
        payload = json.dumps(
            {
                "name": "final_answer",
                "arguments": {"answer": self.answer},
                "thought": "solve quickly",
            }
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
        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=json.dumps(
                {
                    "name": "final_answer",
                    "arguments": {"answer": self.answer},
                    "thought": "solve quickly",
                }
            ),
        )


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


class RuntimeEngineTests(unittest.TestCase):
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
            provider="openai",
            api_key="sk-test",
            model="gpt-5.4",
            max_steps=6,
        )

    def test_has_delta_and_step_normalization(self) -> None:
        service = SmolagentsRuntimeEngine(
            self._config(),
            model_factory=lambda: FakeStreamingModel("ok"),
        )

        result = asyncio.run(service.process_async(
            turn_id="turn-1",
            user_input="say ok",
            auth_context={},
            metadata=RunMetadata(
                workspace_path=str(self.workspace_a), session_key=None
            ),
        ))

        self.assertEqual(result["final_answer"], "ok")
        self.assertGreaterEqual(len(result["deltas"]), 1)
        self.assertGreaterEqual(len(result["session_messages"]), 1)
        assistant_messages = [
            item for item in result["session_messages"] if item.get("role") == "assistant"
        ]
        self.assertGreaterEqual(len(assistant_messages), 1)

    def test_configured_model_execution(self) -> None:
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

        result = asyncio.run(service.process_async(
            turn_id="turn-direct",
            user_input="task",
            auth_context={},
            metadata=RunMetadata(
                workspace_path=str(self.workspace_a), session_key=None
            ),
        ))

        self.assertEqual(result["final_answer"], "direct-ok")

    def test_configured_model_passes_reasoning_effort_to_model_factory(self) -> None:
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

        result = asyncio.run(service.process_async(
            turn_id="turn-direct-reasoning",
            user_input="task",
            auth_context={},
            metadata=RunMetadata(
                workspace_path=str(self.workspace_a), session_key=None
            ),
        ))

        self.assertEqual(result["final_answer"], "direct-ok")
        self.assertEqual(captured["reasoning_effort"], "high")

    def test_configured_model_requires_api_key(self) -> None:
        cfg = replace(
            self._config(),
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_key="",
            model="gpt-4.1",
        )

        service = SmolagentsRuntimeEngine(cfg)

        with self.assertRaises(RuntimeProcessError) as ctx:
            asyncio.run(service.process_async(
                turn_id="turn-direct-no-key",
                user_input="task",
                auth_context={},
                metadata=RunMetadata(
                    workspace_path=str(self.workspace_a), session_key=None
                ),
        ))

        self.assertEqual(ctx.exception.code, "missing_api_key")
        self.assertEqual(ctx.exception.message, "configured api key is missing")

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
            asyncio.run(service.process_async(
                turn_id="turn-provider-failure",
                user_input="task",
                auth_context={},
                metadata=RunMetadata(
                    workspace_path=str(self.workspace_a), session_key=None
                ),
        ))

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

        result = asyncio.run(service.process_async(
            turn_id="turn-3",
            user_input="continue",
            auth_context={},
            metadata=RunMetadata(
                workspace_path=str(self.workspace_a), session_key=None
            ),
        ))

        self.assertEqual(result["final_answer"], "done")

    def test_workspace_session_isolation(self) -> None:
        service = SmolagentsRuntimeEngine(
            self._config(),
            model_factory=lambda: FakeStreamingModel("ok"),
        )

        asyncio.run(service.process_async(
            turn_id="turn-a",
            user_input="task-a",
            auth_context={},
            metadata=RunMetadata(
                workspace_path=str(self.workspace_a), session_key=None
            ),
        ))
        asyncio.run(service.process_async(
            turn_id="turn-b",
            user_input="task-b",
            auth_context={},
            metadata=RunMetadata(
                workspace_path=str(self.workspace_b), session_key=None
            ),
        ))

        self.assertEqual(len(service.sessions.cache), 2)
        keys = sorted(service.sessions.cache.keys())
        self.assertNotEqual(keys[0], keys[1])

    def test_windows_workspace_variants_share_default_session_key(self) -> None:
        cfg = replace(self._config(), tool_os_type="windows")
        service = SmolagentsRuntimeEngine(
            cfg,
            model_factory=lambda: FakeStreamingModel("ok"),
        )

        asyncio.run(service.process_async(
            turn_id="turn-win-1",
            user_input="task-a",
            auth_context={},
            metadata=RunMetadata(
                workspace_path="C:/Workspace/Project", session_key=None
            ),
        ))
        asyncio.run(service.process_async(
            turn_id="turn-win-2",
            user_input="task-b",
            auth_context={},
            metadata=RunMetadata(
                workspace_path="/mnt/c/workspace/project", session_key=None
            ),
        ))
        asyncio.run(service.process_async(
            turn_id="turn-win-3",
            user_input="task-c",
            auth_context={},
            metadata=RunMetadata(
                workspace_path="/cygdrive/c/Workspace/Project", session_key=None
            ),
        ))

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

    def test_runtime_engine_wires_tool_calling_agent_with_managed_code_agent(self) -> None:
        created: dict[str, object] = {}

        class FakeCodeAgent:
            def __init__(self, *args, **kwargs):
                del args
                created["code_kwargs"] = kwargs
                created["code_instance"] = self

        class FakeToolCallingAgent:
            def __init__(self, *args, **kwargs):
                del args
                created["main_kwargs"] = kwargs
                self.memory = type("Memory", (), {"steps": []})()

            def run(self, *args, **kwargs):
                del args, kwargs
                return iter(())

        with patch(
            "nsbot_sidecar.runtime.engine.NativeCodeAgent",
            FakeCodeAgent,
        ), patch(
            "nsbot_sidecar.runtime.engine.NativeToolCallingAgent",
            FakeToolCallingAgent,
        ):
            service = SmolagentsRuntimeEngine(
                self._config(),
                model_factory=lambda: FakeStreamingModel("ok"),
            )
            result = asyncio.run(service.process_async(
                turn_id="turn-managed-agent",
                user_input="task",
                auth_context={},
                metadata=RunMetadata(
                    workspace_path=str(self.workspace_a),
                    session_key=None,
                ),
        ))

        self.assertIsNone(result["final_answer"])
        main_kwargs = created["main_kwargs"]
        code_kwargs = created["code_kwargs"]
        self.assertIn("managed_agents", main_kwargs)
        self.assertEqual(len(main_kwargs["managed_agents"]), 1)
        self.assertIs(main_kwargs["managed_agents"][0], created["code_instance"])
        self.assertIn("non-mutating tools first", str(main_kwargs["instructions"]))
        self.assertIn("Code Subagent Contract", str(code_kwargs["context_prefix"]))
        self.assertIn(
            "cannot be completed efficiently or reliably",
            str(code_kwargs["context_prefix"]),
        )
        self.assertNotEqual(
            str(main_kwargs["context_prefix"]),
            str(code_kwargs["context_prefix"]),
        )
        self.assertTrue(code_kwargs["stream_outputs"])
        self.assertTrue(main_kwargs["stream_outputs"])
        self.assertNotIn("verbosity_level", code_kwargs)
        self.assertNotIn("verbosity_level", main_kwargs)
        self.assertIsNone(code_kwargs["logger"])
        self.assertIsNone(main_kwargs["logger"])

    def test_runtime_engine_disables_console_output_when_configured(self) -> None:
        created: dict[str, object] = {}

        class FakeCodeAgent:
            def __init__(self, *args, **kwargs):
                del args
                created["code_kwargs"] = kwargs
                created["code_instance"] = self

        class FakeToolCallingAgent:
            def __init__(self, *args, **kwargs):
                del args
                created["main_kwargs"] = kwargs
                self.memory = type("Memory", (), {"steps": []})()

            def run(self, *args, **kwargs):
                del args, kwargs
                return iter(())

        with patch(
            "nsbot_sidecar.runtime.engine.NativeCodeAgent",
            FakeCodeAgent,
        ), patch(
            "nsbot_sidecar.runtime.engine.NativeToolCallingAgent",
            FakeToolCallingAgent,
        ):
            service = SmolagentsRuntimeEngine(
                replace(self._config(), allow_console_output=False),
                model_factory=lambda: FakeStreamingModel("ok"),
            )
            asyncio.run(service.process_async(
                turn_id="turn-stream-flags-disabled",
                user_input="task",
                auth_context={},
                metadata=RunMetadata(
                    workspace_path=str(self.workspace_a),
                    session_key=None,
                ),
        ))

        self.assertTrue(created["code_kwargs"]["stream_outputs"])
        self.assertTrue(created["main_kwargs"]["stream_outputs"])
        self.assertNotIn("verbosity_level", created["code_kwargs"])
        self.assertNotIn("verbosity_level", created["main_kwargs"])
        self.assertIsNotNone(created["code_kwargs"]["logger"])
        self.assertIsNotNone(created["main_kwargs"]["logger"])
        self.assertEqual(created["code_kwargs"]["logger"].level, 0)
        self.assertEqual(created["main_kwargs"]["logger"].level, 0)

    def test_runtime_engine_appends_extra_tools_to_workspace_tools(self) -> None:
        created: dict[str, object] = {}
        extra_tool = object()

        class FakeCodeAgent:
            def __init__(self, *args, **kwargs):
                del args
                created["code_tools"] = list(kwargs["tools"])
                created["code_instance"] = self

        class FakeToolCallingAgent:
            def __init__(self, *args, **kwargs):
                del args
                created["main_tools"] = list(kwargs["tools"])
                self.memory = type("Memory", (), {"steps": []})()

            def run(self, *args, **kwargs):
                del args, kwargs
                return iter(())

        with patch(
            "nsbot_sidecar.runtime.engine.NativeCodeAgent",
            FakeCodeAgent,
        ), patch(
            "nsbot_sidecar.runtime.engine.NativeToolCallingAgent",
            FakeToolCallingAgent,
        ):
            service = SmolagentsRuntimeEngine(
                self._config(),
                model_factory=lambda: FakeStreamingModel("ok"),
                extra_tools=[extra_tool],
            )
            asyncio.run(service.process_async(
                turn_id="turn-extra-tools",
                user_input="task",
                auth_context={},
                metadata=RunMetadata(
                    workspace_path=str(self.workspace_a),
                    session_key=None,
                ),
        ))

        self.assertIn(extra_tool, created["code_tools"])
        self.assertIn(extra_tool, created["main_tools"])

    def test_has_delta_when_console_output_disabled(self) -> None:
        service = SmolagentsRuntimeEngine(
            replace(self._config(), allow_console_output=False),
            model_factory=lambda: FakeStreamingModel("silent-ok"),
        )

        result = asyncio.run(service.process_async(
            turn_id="turn-silent-deltas",
            user_input="say ok",
            auth_context={},
            metadata=RunMetadata(
                workspace_path=str(self.workspace_a), session_key=None
            ),
        ))

        self.assertEqual(result["final_answer"], "silent-ok")
        self.assertGreaterEqual(len(result["deltas"]), 1)

    def test_execute_runtime_turn_forwards_event_callback_and_is_cancelled(self) -> None:
        cfg = self._config()
        metadata = RunMetadata(
            workspace_path=str(self.workspace_a),
            session_key="session-1",
        )
        callback = lambda _event: None
        is_cancelled = lambda: False

        with patch(
            "nsbot_sidecar.application.turn_service.create_runtime_engine"
        ) as engine_factory:
            runtime_engine = engine_factory.return_value
            runtime_engine.process_async = AsyncMock(
                return_value={
                    "deltas": [],
                    "session_messages": [],
                    "final_answer": "ok",
                }
            )
            result = asyncio.run(
                execute_runtime_turn(
                    cfg,
                    "run-1",
                    "task",
                    {"auth": "ctx"},
                    metadata,
                    event_callback=callback,
                    is_cancelled=is_cancelled,
                )
            )

        self.assertEqual(result["final_answer"], "ok")
        engine_factory.assert_called_once_with(cfg)
        runtime_engine.process_async.assert_called_once_with(
            turn_id="run-1",
            user_input="task",
            auth_context={"auth": "ctx"},
            metadata=metadata,
            event_callback=callback,
            is_cancelled=is_cancelled,
        )

    def test_collect_tool_results_by_call_id_from_nested_action_output(self) -> None:
        action_output = {
            "items": [
                {
                    "call_id": "call-read",
                    "tool_name": "read",
                    "details": {"truncation": {"truncated": True, "outputLines": 10}},
                    "content": [{"type": "text", "text": "ok"}],
                    "is_error": False,
                },
                {
                    "nested": {
                        "call_id": "call-edit",
                        "details": {"diff": "@@ -1 +1 @@", "firstChangedLine": 3},
                        "error": {"code": "execution_failed", "message": "boom"},
                        "is_error": True,
                    }
                },
                {
                    "call_id": "call-empty",
                    "details": {},
                },
            ]
        }

        results_by_call_id = _collect_tool_results_by_call_id(action_output)
        self.assertEqual(
            results_by_call_id["call-read"]["details"]["truncation"]["outputLines"],
            10,
        )
        self.assertEqual(
            results_by_call_id["call-edit"]["details"]["firstChangedLine"],
            3,
        )
        self.assertEqual(results_by_call_id["call-read"]["toolName"], "read")
        self.assertFalse(results_by_call_id["call-read"]["isError"])
        self.assertEqual(
            results_by_call_id["call-read"]["content"][0]["text"],
            "ok",
        )
        self.assertEqual(
            results_by_call_id["call-edit"]["error"]["message"],
            "boom",
        )
        self.assertTrue(results_by_call_id["call-edit"]["isError"])
        self.assertNotIn("call-empty", results_by_call_id)


if __name__ == "__main__":
    unittest.main()
