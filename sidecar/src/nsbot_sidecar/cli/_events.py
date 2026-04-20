from __future__ import annotations

import json
import re
from typing import Any
import uuid


_TODO_LINE_PATTERN = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+?)\s*$")
_TIMELINE_DEPRECATION_NOTICE = (
    "Deprecated: timeline contains ACP-derived compatibility rows. "
    "Use events for the Codex SDK-like event stream."
)


def _parse_tool_arguments(tool_call: dict[str, Any]) -> Any:
    arguments_text = str(tool_call.get("argumentsText") or "").strip()
    if arguments_text == "":
        return None
    try:
        return json.loads(arguments_text)
    except json.JSONDecodeError:
        return arguments_text


def _tool_content_text(tool_result: dict[str, Any] | None) -> str:
    if not isinstance(tool_result, dict):
        return ""
    content = tool_result.get("content")
    if not isinstance(content, list):
        return ""
    blocks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip().lower() != "text":
            continue
        text = str(item.get("text") or "").strip()
        if text:
            blocks.append(text)
    return "\n".join(blocks)


def _tool_error_text(tool_result: dict[str, Any] | None, fallback: str = "") -> str:
    if isinstance(tool_result, dict):
        error = tool_result.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            if message:
                return message
        if tool_result.get("isError"):
            return _tool_content_text(tool_result) or fallback
    return fallback


def _tool_failed(tool_result: dict[str, Any] | None, fallback_error: str = "") -> bool:
    if fallback_error.strip():
        return True
    if not isinstance(tool_result, dict):
        return False
    if tool_result.get("isError"):
        return True
    error = tool_result.get("error")
    return isinstance(error, dict) and str(error.get("message") or "").strip() != ""


def _infer_todo_items(plan_text: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for line in plan_text.splitlines():
        match = _TODO_LINE_PATTERN.match(line)
        if match is None:
            continue
        text = match.group(1).strip()
        if text:
            matches.append({"text": text, "completed": False})
    if matches:
        return matches

    fallback = plan_text.strip()
    if fallback == "":
        return []
    return [{"text": fallback, "completed": False}]


def _command_text_for_tool(name: str, arguments: Any) -> str:
    if name == "python_exec_agent":
        if isinstance(arguments, dict):
            for key in ("code", "codeAction", "code_action"):
                value = arguments.get(key)
                if isinstance(value, str) and value.strip():
                    return f"python_exec_agent {value.strip()}"
        if isinstance(arguments, str) and arguments.strip():
            return f"python_exec_agent {arguments.strip()}"
        return "python_exec_agent"

    if name == "grep":
        if isinstance(arguments, dict):
            pattern = str(arguments.get("pattern") or "").strip()
            path = str(arguments.get("path") or "").strip()
            parts = ["rg"]
            if pattern:
                parts.append(pattern)
            if path:
                parts.append(path)
            return " ".join(parts)
        return "rg"

    if name == "find":
        if isinstance(arguments, dict):
            pattern = str(arguments.get("pattern") or "").strip()
            path = str(arguments.get("path") or "").strip()
            parts = ["fd"]
            if pattern:
                parts.append(pattern)
            if path:
                parts.append(path)
            return " ".join(parts)
        return "fd"

    return name


def _file_change_item_for_tool(tool_call_id: str, tool_name: str, arguments: Any) -> dict[str, Any]:
    path_value = None
    kind = "update"
    if isinstance(arguments, dict):
        raw_path = arguments.get("path")
        if isinstance(raw_path, str) and raw_path.strip():
            path_value = raw_path.strip()
    if tool_name == "edit":
        kind = "update"
    elif tool_name == "write":
        mutation_kind = None
        if isinstance(arguments, dict):
            mutation_kind = arguments.get("mutationKind")
        if isinstance(mutation_kind, str) and mutation_kind in {"add", "update"}:
            kind = mutation_kind
    changes = []
    if path_value is not None:
        changes.append(
            {
                "path": path_value,
                "kind": kind,
            }
        )
    return {
        "id": tool_call_id,
        "type": "file_change",
        "changes": changes,
        "status": "completed",
    }


def _mcp_tool_item_for_tool(
    tool_call_id: str,
    tool_name: str,
    arguments: Any,
    tool_result: dict[str, Any] | None,
    *,
    failed: bool,
    error_text: str,
) -> dict[str, Any]:
    item = {
        "id": tool_call_id,
        "type": "mcp_tool_call",
        "server": "workspace",
        "tool": tool_name,
        "arguments": arguments,
        "status": "failed" if failed else "completed",
    }
    if failed:
        item["error"] = {"message": error_text or "Tool call failed"}
        return item

    result_payload: dict[str, Any] = {
        "content": tool_result.get("content") if isinstance(tool_result, dict) else [],
        "structured_content": (
            tool_result.get("structuredContent") if isinstance(tool_result, dict) else None
        ),
    }
    item["result"] = result_payload
    return item


def _command_item_for_tool(
    tool_call_id: str,
    tool_name: str,
    arguments: Any,
    tool_result: dict[str, Any] | None,
    *,
    failed: bool,
    error_text: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    aggregated_output = _tool_content_text(tool_result)
    if failed and error_text:
        aggregated_output = error_text if aggregated_output == "" else f"{aggregated_output}\n{error_text}"

    started_item = {
        "id": tool_call_id,
        "type": "command_execution",
        "command": _command_text_for_tool(tool_name, arguments),
        "aggregated_output": "",
        "status": "in_progress",
    }
    completed_item = {
        **started_item,
        "aggregated_output": aggregated_output,
        "status": "failed" if failed else "completed",
    }
    if not failed:
        completed_item["exit_code"] = 0
    return started_item, completed_item


def _append_item_event(events: list[dict[str, Any]], event_type: str, item: dict[str, Any]) -> None:
    events.append({"type": event_type, "item": item})


def _build_codex_thread_events(
    *,
    thread_id: str,
    turn_id: str,
    runtime_events: list[dict[str, Any]],
    runtime_result: dict[str, Any] | None,
    error_message: str | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
    ]
    open_messages: dict[str, dict[str, Any]] = {}
    saw_completed_agent_message = False
    latest_todo_item: dict[str, Any] | None = None
    todo_started = False
    usage_by_step: dict[str, dict[str, int]] = {}

    def finalize_message(step_id: str | None) -> None:
        nonlocal saw_completed_agent_message
        if step_id is None:
            return
        item = open_messages.pop(step_id, None)
        if item is None:
            return
        _append_item_event(events, "item.completed", item)
        saw_completed_agent_message = True

    for runtime_event in runtime_events:
        if not isinstance(runtime_event, dict):
            continue
        event_type = str(runtime_event.get("type") or "")
        payload = runtime_event.get("payload")
        payload_dict = payload if isinstance(payload, dict) else {}

        if event_type == "delta":
            step_id = str(payload_dict.get("step_id") or "").strip()
            text = str(payload_dict.get("text") or "")
            if step_id == "" or text == "":
                continue
            item = open_messages.get(step_id)
            if item is None:
                item = {
                    "id": f"agent-message:{step_id}",
                    "type": "agent_message",
                    "text": text,
                }
                open_messages[step_id] = item
                _append_item_event(events, "item.started", dict(item))
                continue
            item["text"] = f"{item['text']}{text}"
            _append_item_event(events, "item.updated", dict(item))
            continue

        if event_type != "timeline_entry":
            continue

        entry_kind = str(payload_dict.get("entry_kind") or "").strip()
        step_id = str(payload_dict.get("step_id") or "").strip() or None
        finalize_message(step_id)

        if entry_kind == "planning":
            todo_item = {
                "id": f"todo-list:{turn_id}",
                "type": "todo_list",
                "items": _infer_todo_items(str(payload_dict.get("content_text") or "")),
            }
            latest_todo_item = todo_item
            if not todo_started:
                todo_started = True
                _append_item_event(events, "item.started", dict(todo_item))
            else:
                _append_item_event(events, "item.updated", dict(todo_item))
            continue

        if entry_kind != "action":
            continue

        content_json = payload_dict.get("content_json")
        if isinstance(content_json, str):
            try:
                content_json = json.loads(content_json)
            except json.JSONDecodeError:
                content_json = None
        content = content_json if isinstance(content_json, dict) else {}

        thought = str(content.get("thought") or "").strip()
        if thought:
            reasoning_item = {
                "id": f"reasoning:{step_id or uuid.uuid4().hex}",
                "type": "reasoning",
                "text": thought,
            }
            _append_item_event(events, "item.started", dict(reasoning_item))
            _append_item_event(events, "item.completed", reasoning_item)

        if step_id is not None and step_id not in usage_by_step:
            usage_payload = content.get("usage")
            if isinstance(usage_payload, dict):
                usage_by_step[step_id] = {
                    "input_tokens": int(usage_payload.get("inputTokens") or 0),
                    "output_tokens": int(usage_payload.get("outputTokens") or 0),
                }

        tool_results_by_call_id: dict[str, dict[str, Any]] = {}
        tool_results = content.get("toolResults")
        if isinstance(tool_results, list):
            for tool_result in tool_results:
                if not isinstance(tool_result, dict):
                    continue
                call_id = str(tool_result.get("callId") or "").strip()
                if call_id:
                    tool_results_by_call_id[call_id] = tool_result

        action_error = str(content.get("error") or "").strip()
        tool_calls = content.get("toolCalls")
        if not isinstance(tool_calls, list):
            continue

        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = str(tool_call.get("id") or uuid.uuid4().hex).strip()
            tool_name = str(tool_call.get("name") or "tool").strip()
            arguments = _parse_tool_arguments(tool_call)
            tool_result = tool_results_by_call_id.get(tool_call_id)
            failed = _tool_failed(tool_result, action_error)
            tool_error = _tool_error_text(tool_result, action_error)

            if tool_name == "write" and isinstance(arguments, dict) and isinstance(tool_result, dict):
                details = tool_result.get("details")
                if isinstance(details, dict):
                    mutation_kind = details.get("mutationKind")
                    if isinstance(mutation_kind, str) and mutation_kind in {"add", "update"}:
                        arguments = {**arguments, "mutationKind": mutation_kind}

            if tool_name in {"python_exec_agent", "grep", "find"}:
                started_item, completed_item = _command_item_for_tool(
                    tool_call_id,
                    tool_name,
                    arguments,
                    tool_result,
                    failed=failed,
                    error_text=tool_error,
                )
                _append_item_event(events, "item.started", started_item)
                _append_item_event(events, "item.completed", completed_item)
                continue

            if tool_name in {"write", "edit"}:
                file_item = _file_change_item_for_tool(tool_call_id, tool_name, arguments)
                file_item["status"] = "failed" if failed else "completed"
                _append_item_event(events, "item.completed", file_item)
                continue

            started_item = {
                "id": tool_call_id,
                "type": "mcp_tool_call",
                "server": "workspace",
                "tool": tool_name,
                "arguments": arguments,
                "status": "in_progress",
            }
            completed_item = _mcp_tool_item_for_tool(
                tool_call_id,
                tool_name,
                arguments,
                tool_result,
                failed=failed,
                error_text=tool_error,
            )
            _append_item_event(events, "item.started", started_item)
            _append_item_event(events, "item.completed", completed_item)

    for pending_step_id in list(open_messages.keys()):
        finalize_message(pending_step_id)

    final_answer = None
    if isinstance(runtime_result, dict):
        final_answer = str(runtime_result.get("final_answer") or "").strip() or None
    if final_answer and not saw_completed_agent_message:
        final_item = {
            "id": f"agent-message:final:{turn_id}",
            "type": "agent_message",
            "text": final_answer,
        }
        _append_item_event(events, "item.started", dict(final_item))
        _append_item_event(events, "item.completed", final_item)

    if latest_todo_item is not None:
        _append_item_event(events, "item.completed", latest_todo_item)

    if error_message:
        events.append({"type": "turn.failed", "error": {"message": error_message}})
        return events

    usage = {
        "input_tokens": sum(item.get("input_tokens", 0) for item in usage_by_step.values()),
        "cached_input_tokens": 0,
        "output_tokens": sum(item.get("output_tokens", 0) for item in usage_by_step.values()),
    }
    events.append({"type": "turn.completed", "usage": usage})
    return events