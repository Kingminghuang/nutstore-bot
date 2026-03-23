from __future__ import annotations

import inspect
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smolagents.memory import ActionStep, FinalAnswerStep, PlanningStep
from smolagents.models import ChatMessageStreamDelta

from .context_builder import ContextBuildError, ContextBuilder, ContextBuilderConfig, RuntimeInfo
from .direct_model import DirectModel, DirectModelConfig, DirectModelError
from .gateway_model import GatewayAuthError, GatewayModel, GatewayModelConfig
from .local_code_executor import LocalCodeExecutor
from .memory import MemoryConsolidator, MemoryStore
from .native_code_agent import NativeCodeAgent
from .session_manager import SessionManager
from .tools import build_workspace_tools, path_identity, resolve_path_arg


class RuntimeProcessError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RuntimeWorkerConfig:
    gateway_base_url: str
    model_id: str
    ns_bot_home: str
    workspace_path_default: str
    runtime_mode: str = "gateway"
    direct_provider: str | None = None
    direct_base_url: str | None = None
    direct_api_key: str | None = None
    direct_model_id: str | None = None
    direct_request_timeout_ms: int = 60_000
    fd_executable: str | None = None
    rg_executable: str | None = None
    tool_os_type: str | None = None
    max_steps: int = 20


@dataclass(frozen=True)
class RunMetadata:
    workspace_path: str | None = None
    session_key: str | None = None


class CodeAgentRuntimeService:
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
        run_id: str,
        user_input: str,
        auth_context: dict[str, Any],
        metadata: RunMetadata,
    ) -> dict[str, Any]:
        runtime_mode = str(self.config.runtime_mode or "gateway").strip().lower()
        is_direct_mode = runtime_mode == "direct"

        gateway_token = str(auth_context.get("gateway_token") or auth_context.get("gatewayToken") or "").strip()
        if not is_direct_mode and gateway_token == "":
            raise RuntimeProcessError("unauthorized", "gateway token is missing")

        workspace_path = self._resolve_workspace_path(metadata)
        session_key = self._resolve_session_key(metadata, workspace_path)
        session = self.sessions.get_or_create(session_key)

        if is_direct_mode:
            direct_base_url = str(self.config.direct_base_url or "").strip()
            direct_api_key = str(self.config.direct_api_key or "").strip()
            direct_model_id = str(self.config.direct_model_id or self.config.model_id).strip()
            if direct_base_url == "":
                raise RuntimeProcessError("invalid_base_url", "direct base url is missing")
            if direct_api_key == "":
                raise RuntimeProcessError("missing_api_key", "direct api key is missing")
            if direct_model_id == "":
                raise RuntimeProcessError("missing_model_id", "direct model id is missing")

            selected_model = DirectModel(
                DirectModelConfig(
                    provider=str(self.config.direct_provider or "custom"),
                    base_url=direct_base_url,
                    api_key=direct_api_key,
                    model_id=direct_model_id,
                    timeout_seconds=max(1.0, float(self.config.direct_request_timeout_ms) / 1000.0),
                )
            )
        else:
            selected_model = GatewayModel(
                GatewayModelConfig(
                    gateway_base_url=self.config.gateway_base_url,
                    model_id=self.config.model_id,
                    gateway_token=gateway_token,
                )
            )
        consolidation_provider = selected_model

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
                consolidator = self.consolidator_factory(self.sessions, self.memory_store)
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
            # best-effort by design
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
            context_prompt = self.context_builder.build_system_prompt(context_cfg, runtime_info, self.memory_store)
        except ContextBuildError as exc:
            raise RuntimeProcessError("context_build_failed", str(exc)) from exc

        try:
            model = (
                self.model_factory(gateway_token)
                if self.model_factory
                else selected_model
            )
        except (GatewayAuthError, DirectModelError) as exc:
            raise RuntimeProcessError(exc.code, exc.message) from exc
        except Exception as exc:
            raise RuntimeProcessError("runtime_error", str(exc)) from exc

        tools = build_workspace_tools(
            workspace_path,
            fd_executable=self.config.fd_executable,
            rg_executable=self.config.rg_executable,
            os_type=self.config.tool_os_type,
        )
        executor = LocalCodeExecutor(
            run_id=run_id,
            workspace_path=workspace_path,
            timeout_seconds=30,
        )
        agent = NativeCodeAgent(
            tools=tools,
            model=model,
            context_prefix=context_prompt,
            stream_outputs=True,
            max_steps=self.config.max_steps,
            executor=executor,
        )

        deltas: list[dict[str, Any]] = []
        steps: list[dict[str, Any]] = []
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
            stream = agent.run(task, stream=True, reset=True)
            for event in stream:
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
                    continue

                if isinstance(event, PlanningStep):
                    step_id = allocate_step_id(current_step_id)
                    steps.append(
                        {
                            "step_id": step_id,
                            "step_kind": "planning",
                            "model_output": event.plan or "",
                            "observations": [],
                            "error": None,
                            "usage": self._usage_dict(event.token_usage),
                            "duration_ms": self._duration_ms(event),
                            "has_delta": stream_buffer_by_step.get(step_id, "") != "",
                        }
                    )
                    current_step_id = None
                    continue

                if isinstance(event, ActionStep):
                    step_id = allocate_step_id(current_step_id)
                    steps.append(
                        {
                            "step_id": step_id,
                            "step_kind": "action",
                            "model_output": str(event.model_output or ""),
                            "observations": self._observation_list(event.observations),
                            "error": None if event.error is None else str(event.error),
                            "usage": self._usage_dict(event.token_usage),
                            "duration_ms": self._duration_ms(event),
                            "has_delta": stream_buffer_by_step.get(step_id, "") != "",
                        }
                    )
                    if event.is_final_answer and event.action_output is not None:
                        final_answer = str(event.action_output)
                    current_step_id = None
                    continue

                if isinstance(event, FinalAnswerStep):
                    final_answer = str(event.output)
                    continue
        except (GatewayAuthError, DirectModelError) as exc:
            raise RuntimeProcessError(exc.code, exc.message) from exc
        except Exception as exc:
            text = str(exc)
            lowered = text.lower()
            if "token_expired" in lowered or "token has expired" in lowered or "token expired" in lowered:
                raise RuntimeProcessError("token_expired", text) from exc
            if "unauthorized" in lowered:
                raise RuntimeProcessError("unauthorized", text) from exc
            raise RuntimeProcessError("runtime_error", text) from exc
        finally:
            executor.release_run()

        session.add_message("user", user_input)
        session.add_message("assistant", final_answer or "")
        self.sessions.save(session)

        try:
            consolidator.maybe_consolidate_by_tokens(session)
        except Exception:
            pass

        return {
            "deltas": deltas,
            "steps": steps,
            "final_answer": final_answer,
        }

    def _resolve_workspace_path(self, metadata: RunMetadata) -> str:
        raw = metadata.workspace_path or self.config.workspace_path_default
        if self.config.tool_os_type and str(self.config.tool_os_type).strip().lower().startswith("win"):
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
