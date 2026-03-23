from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

SAVE_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "save_memory",
        "description": "Save the memory consolidation result to persistent storage.",
        "parameters": {
            "type": "object",
            "properties": {
                "history_entry": {
                    "type": "string",
                    "description": (
                        "A paragraph summarizing key events, decisions, and topics. "
                        "Start with [YYYY-MM-DD HH:MM]."
                    ),
                },
                "memory_update": {
                    "type": "string",
                    "description": (
                        "The full updated long-term memory markdown. "
                        "Return the unchanged memory if nothing new was learned."
                    ),
                },
            },
            "required": ["history_entry", "memory_update"],
        },
    },
}

_SESSION_LOCKS: dict[str, threading.Lock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def ensure_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def normalize_save_memory_args(args: Any) -> dict[str, Any] | None:
    if isinstance(args, str):
        args = json.loads(args)
    if isinstance(args, list):
        if args and isinstance(args[0], dict):
            return args[0]
        return None
    if isinstance(args, dict):
        return args
    return None


def _extract_tool_names(message: dict[str, Any]) -> list[str]:
    raw_tool_calls = message.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return []

    tool_names: list[str] = []
    for raw_tool_call in raw_tool_calls:
        if not isinstance(raw_tool_call, dict):
            continue
        function_data = raw_tool_call.get("function")
        if isinstance(function_data, dict) and function_data.get("name"):
            tool_names.append(str(function_data["name"]))
            continue
        if raw_tool_call.get("name"):
            tool_names.append(str(raw_tool_call["name"]))
    return tool_names


def format_messages_for_consolidation(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        content = message.get("content")
        if content in (None, ""):
            continue
        timestamp = str(message.get("timestamp") or "?")[:16]
        role = str(message.get("role") or "assistant").upper()
        tool_names = _extract_tool_names(message)
        tools_suffix = ""
        if tool_names:
            tools_suffix = " [tools: " + ", ".join(tool_names) + "]"
        lines.append(f"[{timestamp}] {role}{tools_suffix}: {ensure_text(content)}")
    return "\n".join(lines)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    encoded = json.dumps(
        {
            "role": message.get("role"),
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls"),
            "tool_call_id": message.get("tool_call_id"),
            "name": message.get("name"),
        },
        ensure_ascii=False,
    )
    return max(1, len(encoded) // 4)


@dataclass
class MemoryArtifact:
    memory_dir: str
    memory_file: str
    history_file: str
    long_term_text: str


class MemoryStore:
    def __init__(self, ns_bot_home: str):
        self.memory_dir = Path(ns_bot_home).expanduser().resolve() / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"

    def read_long_term(self) -> str:
        if not self.memory_file.exists():
            return ""
        return self.memory_file.read_text(encoding="utf-8")

    def write_long_term(self, content: str) -> None:
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        with self.history_file.open("a", encoding="utf-8") as handle:
            handle.write(entry)
            handle.write("\n\n")

    def get_memory_context(self) -> str:
        long_term = self.read_long_term()
        if long_term.strip() == "":
            return ""
        return "## Long-term Memory\n" + long_term

    def consolidate(self, messages: list[dict[str, Any]], provider: Any, model: str | None) -> bool:
        if not messages:
            return True

        current_memory = self.read_long_term()
        prompt = (
            "Process this conversation and call the save_memory tool with your consolidation.\n\n"
            "## Current Long-term Memory\n"
            f"{current_memory or '(empty)'}\n\n"
            "## Conversation to Process\n"
            f"{format_messages_for_consolidation(messages)}"
        )

        try:
            response = provider.chat_with_retry(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a memory consolidation agent. "
                            "Call the save_memory tool with your consolidation of the conversation."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                tools=[SAVE_MEMORY_TOOL],
                model=model,
                tool_choice="required",
            )

            tool_calls = list(getattr(response, "tool_calls", []) or [])
            if not getattr(response, "has_tool_calls", bool(tool_calls)):
                LOGGER.warning("Memory consolidation response is missing tool calls")
                return False

            args = normalize_save_memory_args(tool_calls[0].arguments)
            if args is None:
                LOGGER.warning("Memory consolidation returned invalid tool arguments")
                return False

            history_entry = args.get("history_entry")
            if history_entry:
                self.append_history(ensure_text(history_entry))

            memory_update = args.get("memory_update")
            if memory_update:
                updated_text = ensure_text(memory_update)
                if updated_text != current_memory:
                    self.write_long_term(updated_text)

            LOGGER.info("Memory consolidation completed successfully")
            return True
        except Exception:
            LOGGER.exception("Memory consolidation failed")
            return False

    def artifact(self) -> MemoryArtifact:
        return MemoryArtifact(
            memory_dir=str(self.memory_dir),
            memory_file=str(self.memory_file),
            history_file=str(self.history_file),
            long_term_text=self.read_long_term(),
        )


class MemoryConsolidator:
    MAX_CONSOLIDATION_ROUNDS = 5

    def __init__(
        self,
        sessions,
        memory_store: MemoryStore,
        *,
        provider: Any | None = None,
        model: str | None = None,
        context_window_tokens: int = 120000,
        fail_on_call: bool = False,
    ):
        self.sessions = sessions
        self.memory_store = memory_store
        self.provider = provider
        self.model = model
        self.context_window_tokens = context_window_tokens
        self.fail_on_call = fail_on_call

    def get_lock(self, session_key: str) -> threading.Lock:
        with _SESSION_LOCKS_GUARD:
            if session_key not in _SESSION_LOCKS:
                _SESSION_LOCKS[session_key] = threading.Lock()
            return _SESSION_LOCKS[session_key]

    def consolidate_messages(self, messages: list[dict[str, Any]]) -> bool:
        if self.fail_on_call:
            raise RuntimeError("forced consolidation failure")
        if self.provider is None:
            LOGGER.warning("Memory consolidator has no provider configured")
            return False
        return self.memory_store.consolidate(messages, self.provider, self.model)

    def pick_consolidation_boundary(self, session, tokens_to_remove: int) -> tuple[int, int] | None:
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]

            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary

            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    def estimate_session_prompt_tokens(self, session) -> tuple[int, str]:
        history = session.get_history(max_messages=0)
        source = "\n".join(
            f"{item.get('role', 'assistant')}: {ensure_text(item.get('content', ''))}" for item in history
        )
        estimate = max(0, len(source) // 4)
        return estimate, source

    def maybe_consolidate_by_tokens(self, session) -> None:
        if self.fail_on_call:
            raise RuntimeError("forced consolidation failure")
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        with lock:
            target = self.context_window_tokens // 2
            estimated, _ = self.estimate_session_prompt_tokens(session)
            if estimated <= 0 or estimated < self.context_window_tokens:
                return

            for _ in range(self.MAX_CONSOLIDATION_ROUNDS):
                if estimated <= target:
                    return

                boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                if boundary is None:
                    LOGGER.debug("No safe consolidation boundary found", extra={"session_key": session.key})
                    return

                end_idx, _removed_tokens = boundary
                chunk = session.messages[session.last_consolidated:end_idx]
                if not chunk:
                    return

                ok = self.consolidate_messages(chunk)
                if not ok:
                    return

                session.last_consolidated = end_idx
                self.sessions.save(session)
                estimated, _ = self.estimate_session_prompt_tokens(session)
                if estimated <= 0:
                    return

    def archive_unconsolidated(self, session) -> bool:
        if self.fail_on_call:
            return False

        lock = self.get_lock(session.key)
        with lock:
            snapshot = session.messages[session.last_consolidated:]
            if not snapshot:
                return True
            return self.consolidate_messages(snapshot)
