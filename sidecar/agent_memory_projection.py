from __future__ import annotations

import json
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


def project_agent_memory_to_timeline_entries(
    agent_memory,
    *,
    run_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    action_index = 0
    planning_index = 0

    for step in agent_memory.steps:
        if isinstance(step, TaskStep):
            entries.append(
                {
                    "session_id": session_id,
                    "run_id": run_id,
                    "entry_kind": "user_input",
                    "display_role": "user",
                    "step_id": None,
                    "step_number": None,
                    "content_text": step.task,
                    "content_json": None,
                }
            )
            continue

        if isinstance(step, PlanningStep):
            planning_index += 1
            entries.append(
                {
                    "session_id": session_id,
                    "run_id": run_id,
                    "entry_kind": "planning",
                    "display_role": "assistant",
                    "step_id": f"planning-{planning_index}",
                    "step_number": None,
                    "content_text": step.plan,
                    "content_json": None,
                }
            )
            continue

        if isinstance(step, ActionStep):
            action_index += 1
            tool_calls = []
            for tool_call in step.tool_calls or []:
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
            content_json = json.dumps(
                {
                    "toolCalls": tool_calls,
                    "observations": [
                        line
                        for line in (step.observations or "").splitlines()
                        if line.strip()
                    ],
                    "codeAction": step.code_action,
                    "actionOutput": step.action_output,
                    "error": None if step.error is None else str(step.error),
                    "usage": {
                        "inputTokens": int(
                            getattr(step.token_usage, "input_tokens", 0) or 0
                        )
                        if step.token_usage is not None
                        else 0,
                        "outputTokens": int(
                            getattr(step.token_usage, "output_tokens", 0) or 0
                        )
                        if step.token_usage is not None
                        else 0,
                        "reasoningTokens": 0,
                    },
                    "durationMs": 0
                    if step.timing is None or step.timing.duration is None
                    else max(0, int(float(step.timing.duration) * 1000)),
                },
                ensure_ascii=False,
            )
            entries.append(
                {
                    "session_id": session_id,
                    "run_id": run_id,
                    "entry_kind": "action",
                    "display_role": "assistant",
                    "step_id": f"action-{step.step_number}",
                    "step_number": step.step_number,
                    "content_text": None,
                    "content_json": content_json,
                }
            )

    return entries


def project_final_answer_to_timeline_entry(
    final_answer: str,
    *,
    run_id: str,
    session_id: str,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "run_id": run_id,
        "entry_kind": "final_answer",
        "display_role": "assistant",
        "step_id": None,
        "step_number": None,
        "content_text": final_answer,
        "content_json": None,
    }


def project_system_notice_to_timeline_entry(
    notice: str,
    *,
    run_id: str,
    session_id: str,
    notice_code: str,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "run_id": run_id,
        "entry_kind": "system_notice",
        "display_role": "system",
        "step_id": None,
        "step_number": None,
        "content_text": notice,
        "content_json": json.dumps({"noticeCode": notice_code}, ensure_ascii=False),
    }
