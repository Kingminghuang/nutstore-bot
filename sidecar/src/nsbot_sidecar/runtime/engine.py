from __future__ import annotations

import io
import inspect
import json
import logging
import platform
from pathlib import Path
from typing import Any, Callable, cast

from rich.console import Console
from smolagents.memory import ActionStep, FinalAnswerStep, PlanningStep
from smolagents.models import ChatMessageStreamDelta
from smolagents.monitoring import AgentLogger, LogLevel

from nsbot_sidecar.domain.agent_memory_projection import (
    extract_action_thought,
    project_agent_memory_to_session_messages,
    project_final_answer_to_session_message,
)
from nsbot_sidecar.providers.direct_model import DirectModel, DirectModelConfig, DirectModelError
from nsbot_sidecar.providers.provider_catalog import BUILTIN_PROVIDERS
from nsbot_sidecar.runtime.context_builder import (
    SECTION_SEPARATOR,
    ContextBuildError,
    ContextBuilder,
    ContextBuilderConfig,
    RuntimeInfo,
)
from nsbot_sidecar.runtime.local_code_executor import LocalCodeExecutor
from nsbot_sidecar.runtime.memory import MemoryConsolidator, MemoryStore
from nsbot_sidecar.runtime.native_code_agent import (
    NativeCodeAgent,
    NativeToolCallingAgent,
)
from nsbot_sidecar.runtime.session_manager import SessionManager
from nsbot_sidecar.runtime.tools import build_workspace_tools, path_identity, resolve_path_arg
from nsbot_sidecar.runtime.types import (
    RunMetadata,
    RuntimeCancelledError,
    RuntimeEngine,
    RuntimeProcessError,
    RuntimeResult,
    RuntimeWorkerConfig,
)

WORKSPACE_BASED_INSTRUCTION = (
    "DO NOT use any web search tool; you can only use the tools provided. "
    "Complete tasks based on files in the workspace. Prefer non-mutating tools "
    "first in this order: read, grep, find, ls. Only use edit/write after enough "
    "evidence is collected and the target change is clear."
)
LOGGER = logging.getLogger(__name__)


def _build_agent_logger(*, allow_console_output: bool) -> AgentLogger | None:
    if allow_console_output:
        return None
    return AgentLogger(
        level=LogLevel.ERROR,
        console=Console(
            file=io.StringIO(),
            highlight=False,
            force_terminal=False,
            color_system=None,
        ),
    )


class SmolagentsRuntimeEngine:
    def __init__(
        self,
        config: RuntimeWorkerConfig,
        *,
        model_factory=None,
        consolidator_factory=None,
    ):
        self.config = config
        self.context_builder = ContextBuilder()
        self.sessions = SessionManager(config.ns_bot_home)
        self.memory_store = MemoryStore(config.ns_bot_home)
        self.model_factory = model_factory
        self.consolidator_factory = consolidator_factory

    def process(
        self,
        turn_id: str,
        user_input: str,
        auth_context: dict[str, Any],
        metadata: RunMetadata,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        permission_requester: Callable[[dict[str, Any]], str] | None = None,
    ) -> RuntimeResult:
        del auth_context

        workspace_path = self._resolve_workspace_path(metadata)
        session_key = self._resolve_session_key(metadata, workspace_path)
        session = self.sessions.get_or_create(session_key)

        provider = str(self.config.provider or "custom").strip().lower()
        base_url = str(self.config.base_url or "").strip()
        api_key = str(self.config.api_key or "").strip()
        model = str(self.config.model or self.config.model_id).strip()
        if provider not in BUILTIN_PROVIDERS and base_url == "":
            raise RuntimeProcessError(
                "invalid_base_url", "configured base url is missing"
            )
        if api_key == "":
            raise RuntimeProcessError(
                "missing_api_key", "configured api key is missing"
            )
        if model == "":
            raise RuntimeProcessError(
                "missing_model_id", "configured model id is missing"
            )

        resolved_model_config = DirectModelConfig(
            provider=str(self.config.provider or "custom"),
            base_url=base_url,
            api_key=api_key,
            model_id=model,
            reasoning_effort=self.config.direct_reasoning_effort,
            timeout_seconds=max(1.0, float(self.config.request_timeout_ms) / 1000.0),
        )
        consolidation_provider = None

        if self.consolidator_factory:
            signature = inspect.signature(self.consolidator_factory)
            accepts_provider = "provider" in signature.parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            if accepts_provider:
                consolidator = self.consolidator_factory(
                    self.sessions,
                    self.memory_store,
                    provider=consolidation_provider,
                    model=self.config.model_id,
                )
            else:
                consolidator = self.consolidator_factory(
                    self.sessions, self.memory_store
                )
        else:
            consolidator = MemoryConsolidator(
                self.sessions,
                self.memory_store,
                provider=consolidation_provider,
                model=self.config.model_id,
            )

        try:
            consolidator.maybe_consolidate_by_tokens(session)
        except Exception:
            pass

        runtime_info = RuntimeInfo(
            os_name=platform.system(),
            arch=platform.machine() or "unknown",
            python_version=platform.python_version(),
        )
        context_cfg = ContextBuilderConfig(
            ns_bot_home=self.config.ns_bot_home,
            workspace_path=workspace_path,
        )

        try:
            context_prompt = self.context_builder.build_system_prompt(
                context_cfg, runtime_info, self.memory_store
            )
        except ContextBuildError as exc:
            raise RuntimeProcessError("context_build_failed", str(exc)) from exc

        try:
            model = self._create_model(resolved_model_config)
        except DirectModelError as exc:
            raise RuntimeProcessError(exc.code, exc.message) from exc
        except Exception as exc:
            raise RuntimeProcessError("runtime_error", str(exc)) from exc
        consolidation_provider = model

        tools = build_workspace_tools(
            workspace_path,
            fd_executable=self.config.fd_executable,
            rg_executable=self.config.rg_executable,
            os_type=self.config.tool_os_type,
            permission_requester=permission_requester,
            auto_allow=permission_requester is None,
        )
        code_executor = LocalCodeExecutor(
            turn_id=turn_id,
            workspace_path=workspace_path,
            timeout_seconds=30,
            permission_requester=permission_requester,
            auto_allow=permission_requester is None,
        )
        code_context_prefix = _build_code_context_prefix(
            context_prompt=context_prompt,
            workspace_path=workspace_path,
        )
        allow_console_output = self.config.allow_console_output
        code_agent_logger = _build_agent_logger(
            allow_console_output=allow_console_output
        )
        main_agent_logger = _build_agent_logger(
            allow_console_output=allow_console_output
        )

        code_agent = NativeCodeAgent(
            tools=tools,
            model=model,
            context_prefix=code_context_prefix,
            stream_outputs=True,
            logger=code_agent_logger,
            max_steps=self.config.max_steps,
            executor=code_executor,
            name="python_exec_agent",
            description=(
                "Execute Python for calculations, data shaping, and temporary "
                "scripted analysis when normal tools are insufficient."
            ),
        )
        agent = NativeToolCallingAgent(
            tools=tools,
            model=model,
            context_prefix=context_prompt,
            instructions=WORKSPACE_BASED_INSTRUCTION,
            stream_outputs=True,
            logger=main_agent_logger,
            max_steps=self.config.max_steps,
            managed_agents=[code_agent],
        )

        deltas: list[dict[str, Any]] = []
        stream_buffer_by_step: dict[str, str] = {}
        current_step_id: str | None = None
        step_index = 0
        final_answer: str | None = None

        def allocate_step_id(existing: str | None) -> str:
            nonlocal step_index
            if existing is not None:
                return existing
            step_index += 1
            return f"step-{step_index}"

        task = self._compose_task_with_history(user_input, session)

        try:
            stream: Any = agent.run(task, stream=True, reset=True)
            for event in stream:
                if is_cancelled is not None and is_cancelled():
                    raise RuntimeCancelledError()
                if isinstance(event, ChatMessageStreamDelta):
                    if event.content is None or event.content == "":
                        continue
                    current_step_id = allocate_step_id(current_step_id)
                    stream_buffer_by_step.setdefault(current_step_id, "")
                    stream_buffer_by_step[current_step_id] += event.content
                    deltas.append(
                        {
                            "step_id": current_step_id,
                            "text": event.content,
                        }
                    )
                    if event_callback is not None:
                        event_callback(
                            {
                                "type": "delta",
                                "payload": {
                                    "step_id": current_step_id,
                                    "text": event.content,
                                },
                            }
                        )
                    continue

                if isinstance(event, PlanningStep):
                    step_id = allocate_step_id(current_step_id)
                    timeline_entry_payload = {
                        "session_id": session_key,
                        "turn_id": turn_id,
                        "entry_kind": "planning",
                        "display_role": "assistant",
                        "step_id": step_id,
                        "step_number": None,
                        "content_text": event.plan or "",
                        "content_json": None,
                    }
                    if event_callback is not None:
                        event_callback(
                            {
                                "type": "timeline_entry",
                                "payload": timeline_entry_payload,
                            }
                        )
                    continue

                if isinstance(event, ActionStep):
                    step_id = allocate_step_id(current_step_id)
                    usage = self._usage_dict(event.token_usage)
                    stream_step_text = stream_buffer_by_step.get(step_id)
                    extracted_thought = extract_action_thought(event.model_output)
                    thought_source = "model_output"
                    if extracted_thought is None and stream_step_text:
                        extracted_thought = extract_action_thought(stream_step_text)
                        thought_source = (
                            "run.delta" if extracted_thought is not None else "none"
                        )
                    elif extracted_thought is None:
                        thought_source = "none"

                    LOGGER.info(
                        "ActionStep thought extraction: turn_id=%s step_id=%s source=%s has_thought=%s preview=%s",
                        turn_id,
                        step_id,
                        thought_source,
                        extracted_thought is not None,
                        (
                            (extracted_thought or "")[:120]
                            if extracted_thought is not None
                            else ""
                        ),
                    )
                    tool_calls: list[dict[str, Any]] = []
                    for tool_call in event.tool_calls or []:
                        tool_calls.append(
                            {
                                "id": tool_call.id,
                                "name": tool_call.name,
                                "argumentsText": json.dumps(
                                    tool_call.arguments, ensure_ascii=False
                                )
                                if not isinstance(tool_call.arguments, str)
                                else tool_call.arguments,
                            }
                        )
                    timeline_entry_payload = {
                        "session_id": session_key,
                        "turn_id": turn_id,
                        "entry_kind": "action",
                        "display_role": "assistant",
                        "step_id": step_id,
                        "step_number": int(event.step_number),
                        "content_text": None,
                        "content_json": json.dumps(
                            {
                                "thought": extracted_thought,
                                "toolCalls": tool_calls,
                                "observations": self._observation_list(
                                    event.observations
                                ),
                                "codeAction": None
                                if event.code_action is None
                                else str(event.code_action),
                                "actionOutput": _serialize_action_output(
                                    event.action_output
                                ),
                                "error": None
                                if event.error is None
                                else str(event.error),
                                "usage": {
                                    "inputTokens": usage.get("input_tokens", 0),
                                    "outputTokens": usage.get("output_tokens", 0),
                                    "reasoningTokens": usage.get("reasoning_tokens", 0),
                                },
                                "durationMs": self._duration_ms(event),
                            },
                            ensure_ascii=False,
                        ),
                    }
                    if event_callback is not None:
                        event_callback(
                            {
                                "type": "timeline_entry",
                                "payload": timeline_entry_payload,
                            }
                        )
                    if event.is_final_answer and event.action_output is not None:
                        final_answer = str(event.action_output)
                    current_step_id = None
                    continue

                if isinstance(event, FinalAnswerStep):
                    final_answer = str(event.output)
                    continue
        except DirectModelError as exc:
            raise RuntimeProcessError(exc.code, exc.message) from exc
        except Exception as exc:
            text = str(exc)
            lowered = text.lower()
            if "unauthorized" in lowered:
                raise RuntimeProcessError("unauthorized", text) from exc
            raise RuntimeProcessError("runtime_error", text) from exc
        finally:
            code_executor.release_run()

        projected_messages = project_agent_memory_to_session_messages(
            agent.memory, turn_id=turn_id
        )
        if final_answer is not None:
            projected_messages.append(
                project_final_answer_to_session_message(final_answer, turn_id=turn_id)
            )
        session.append_messages(projected_messages)
        self.sessions.save(session)

        try:
            consolidator.maybe_consolidate_by_tokens(session)
        except Exception:
            pass

        return cast(
            RuntimeResult,
            {
                "deltas": deltas,
                "final_answer": final_answer,
                "session_messages": projected_messages,
            },
        )

    def _resolve_workspace_path(self, metadata: RunMetadata) -> str:
        raw = metadata.workspace_path or self.config.workspace_path_default
        if self.config.tool_os_type and str(
            self.config.tool_os_type
        ).strip().lower().startswith("win"):
            return resolve_path_arg(raw, raw, self.config.tool_os_type)
        return str(Path(raw).expanduser().resolve())

    def _resolve_session_key(self, metadata: RunMetadata, workspace_path: str) -> str:
        if metadata.session_key:
            return metadata.session_key
        return f"workspace:{path_identity(workspace_path, self.config.tool_os_type)}"

    def _compose_task_with_history(self, user_input: str, session) -> str:
        history = session.get_history(max_messages=120)
        if not history:
            return user_input

        lines = ["# Conversation History"]
        for item in history[-20:]:
            role = item.get("role", "assistant")
            content = str(item.get("content", ""))
            lines.append(f"- {role}: {content}")
        return user_input + "\n\n" + "\n".join(lines)

    def _usage_dict(self, token_usage) -> dict[str, int]:
        if token_usage is None:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
            }
        return {
            "input_tokens": int(getattr(token_usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(token_usage, "output_tokens", 0) or 0),
            "reasoning_tokens": 0,
        }

    def _duration_ms(self, step) -> int:
        timing = getattr(step, "timing", None)
        if timing is None or timing.duration is None:
            return 0
        return max(0, int(float(timing.duration) * 1000))

    def _observation_list(self, raw: str | None) -> list[str]:
        if raw is None:
            return []
        return [line for line in raw.splitlines() if line.strip() != ""]

    def _create_model(self, model_config: DirectModelConfig):
        if self.model_factory is None:
            return DirectModel(model_config)

        signature = inspect.signature(self.model_factory)
        accepts_argument = any(
            parameter.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            )
            for parameter in signature.parameters.values()
        ) or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if accepts_argument:
            return self.model_factory(model_config)
        return self.model_factory()


def _serialize_action_output(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def _build_code_context_prefix(*, context_prompt: str, workspace_path: str) -> str:
    identity_layer = context_prompt.split(SECTION_SEPARATOR, 1)[0].strip()
    contract = (
        "## Code Subagent Contract\n"
        "- You are a Python execution specialist.\n"
        "- Treat Python execution as a fallback when tasks cannot be completed efficiently or reliably "
        "with workspace tools (`read/grep/find/ls`).\n"
        "- Typical fallback cases include computation, data transformation, and script-style workflows.\n"
        "- If workspace tools are sufficient, do not execute Python.\n"
        f"- The workspace root is: {workspace_path}"
    )
    return identity_layer + "\n\n" + contract


def create_runtime_engine(
    config: RuntimeWorkerConfig,
    *,
    model_factory=None,
    consolidator_factory=None,
) -> RuntimeEngine:
    return SmolagentsRuntimeEngine(
        config,
        model_factory=model_factory,
        consolidator_factory=consolidator_factory,
    )
