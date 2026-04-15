from __future__ import annotations

import json
import re
from typing import Any

from smolagents.memory import ActionStep, FinalAnswerStep, PlanningStep, TaskStep
from smolagents.models import ChatMessage, MessageRole


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return str(content)


def _message_role_to_text(role: MessageRole | str) -> str:
    value = role.value if isinstance(role, MessageRole) else str(role)
    if value in {"user", "assistant", "system", "tool-call", "tool-response"}:
        return value
    return "assistant"


def extract_action_thought(model_output: Any) -> str | None:
    raw_text = _message_content_to_text(model_output).strip()
    if raw_text == "":
        return None

    # Structured output path: {"thought": "...", "code": "..."}
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        thought_value = parsed.get("thought")
        if isinstance(thought_value, str):
            normalized = thought_value.strip()
            if normalized:
                return normalized

    # Fallback path for plain text model output containing Thought + code block.
    match = re.search(
        r"Thought\s*:\s*(.*?)(?=\n\s*(?:<code>|```)|\Z)",
        raw_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    thought_text = match.group(1).strip()
    return thought_text or None


def project_chat_message_to_session_message(
    message: ChatMessage,
    *,
    run_id: str,
    step_id: str | None,
    source_kind: str,
    internal: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": _message_role_to_text(message.role),
        "content": _message_content_to_text(message.content),
        "run_id": run_id,
        "step_id": step_id,
        "source_kind": source_kind,
        "internal": internal,
    }
    if getattr(message, "tool_calls", None):
        payload["tool_calls"] = [
            tool_call.dict() for tool_call in message.tool_calls or []
        ]
    return payload


def project_agent_memory_to_session_messages(
    agent_memory, *, run_id: str
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for step in agent_memory.steps:
        if isinstance(step, TaskStep):
            step_id = None
            source_kind = "task"
        elif isinstance(step, PlanningStep):
            step_id = f"planning-{len(projected) + 1}"
            source_kind = "planning"
        elif isinstance(step, ActionStep):
            step_id = f"action-{step.step_number}"
            source_kind = "action"
        else:
            continue
        for message in step.to_messages(summary_mode=False):
            projected.append(
                project_chat_message_to_session_message(
                    message,
                    run_id=run_id,
                    step_id=step_id,
                    source_kind=source_kind,
                    internal=False,
                )
            )
    return projected


def project_final_answer_to_session_message(
    final_answer: str, *, run_id: str
) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": final_answer,
        "run_id": run_id,
        "step_id": None,
        "source_kind": "final_answer",
        "internal": False,
    }

