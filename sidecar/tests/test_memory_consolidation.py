from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memory import (
    MemoryConsolidator,
    MemoryStore,
    ensure_text,
    normalize_save_memory_args,
)
from session_manager import Session


@dataclass
class FakeToolCall:
    arguments: Any
    id: str = "tool-1"
    name: str = "save_memory"


@dataclass
class FakeResponse:
    tool_calls: list[FakeToolCall]
    has_tool_calls: bool


class FakeProvider:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat_with_retry(self, *, messages, tools, model, tool_choice):
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "model": model,
                "tool_choice": tool_choice,
            }
        )
        return self.responses.pop(0)


class FakeSessions:
    def __init__(self) -> None:
        self.saved_watermarks: list[int] = []

    def save(self, session: Session) -> None:
        self.saved_watermarks.append(session.last_consolidated)


class SpyMemoryStore(MemoryStore):
    def __init__(self, ns_bot_home: str):
        super().__init__(ns_bot_home)
        self.write_calls = 0
        self.history_calls = 0

    def write_long_term(self, content: str) -> None:
        self.write_calls += 1
        super().write_long_term(content)

    def append_history(self, entry: str) -> None:
        self.history_calls += 1
        super().append_history(entry)


class MemoryConsolidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="memory-consolidation-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ensure_text_and_normalize_save_memory_args(self) -> None:
        self.assertEqual(ensure_text("hello"), "hello")
        self.assertEqual(ensure_text({"x": 1}), '{"x": 1}')
        self.assertEqual(normalize_save_memory_args('{"history_entry":"h","memory_update":"m"}')["history_entry"], "h")
        self.assertEqual(normalize_save_memory_args([{"history_entry": "h2", "memory_update": "m2"}])["memory_update"], "m2")
        self.assertEqual(normalize_save_memory_args({"history_entry": "h3", "memory_update": "m3"})["history_entry"], "h3")
        self.assertIsNone(normalize_save_memory_args([1, 2, 3]))
        self.assertIsNone(normalize_save_memory_args(42))

    def test_get_memory_context_wraps_non_empty_memory(self) -> None:
        store = MemoryStore(str(self.temp_dir))
        self.assertEqual(store.get_memory_context(), "")
        store.write_long_term("remember this")
        self.assertEqual(store.get_memory_context(), "## Long-term Memory\nremember this")

    def test_consolidate_returns_false_when_tool_call_is_missing(self) -> None:
        store = MemoryStore(str(self.temp_dir))
        provider = FakeProvider([FakeResponse(tool_calls=[], has_tool_calls=False)])

        ok = store.consolidate(
            [{"role": "user", "content": "hello", "timestamp": "2026-03-17T10:00:00Z"}],
            provider,
            "gpt-5.4",
        )

        self.assertFalse(ok)
        self.assertFalse(store.history_file.exists())
        self.assertFalse(store.memory_file.exists())

    def test_consolidate_uses_first_tool_call_and_only_writes_changed_memory(self) -> None:
        store = SpyMemoryStore(str(self.temp_dir))
        store.write_long_term("old memory")
        store.write_calls = 0
        provider = FakeProvider(
            [
                FakeResponse(
                    tool_calls=[
                        FakeToolCall(
                            arguments='{"history_entry":"[2026-03-17 10:00] did work","memory_update":"new memory"}'
                        ),
                        FakeToolCall(
                            arguments='{"history_entry":"ignored","memory_update":"ignored"}',
                            id="tool-2",
                        ),
                    ],
                    has_tool_calls=True,
                )
            ]
        )

        ok = store.consolidate(
            [{"role": "assistant", "content": "result", "timestamp": "2026-03-17T10:00:00Z"}],
            provider,
            "gpt-5.4",
        )

        self.assertTrue(ok)
        self.assertEqual(provider.calls[0]["tool_choice"], "required")
        self.assertEqual(provider.calls[0]["model"], "gpt-5.4")
        self.assertEqual(store.read_long_term(), "new memory")
        self.assertEqual(store.history_file.read_text(encoding="utf-8"), "[2026-03-17 10:00] did work\n\n")
        self.assertEqual(store.write_calls, 1)
        self.assertEqual(store.history_calls, 1)

    def test_consolidate_does_not_rewrite_memory_when_unchanged(self) -> None:
        store = SpyMemoryStore(str(self.temp_dir))
        store.write_long_term("same memory")
        store.write_calls = 0
        provider = FakeProvider(
            [
                FakeResponse(
                    tool_calls=[FakeToolCall(arguments={"history_entry": "", "memory_update": "same memory"})],
                    has_tool_calls=True,
                )
            ]
        )

        ok = store.consolidate(
            [{"role": "assistant", "content": "result", "timestamp": "2026-03-17T10:00:00Z"}],
            provider,
            "gpt-5.4",
        )

        self.assertTrue(ok)
        self.assertEqual(store.write_calls, 0)
        self.assertFalse(store.history_file.exists())

    def test_pick_consolidation_boundary_respects_user_turn_boundary(self) -> None:
        consolidator = MemoryConsolidator(FakeSessions(), MemoryStore(str(self.temp_dir)))
        session = Session(
            key="cli:boundary",
            messages=[
                {"role": "assistant", "content": "A" * 50},
                {"role": "assistant", "content": "B" * 50},
                {"role": "user", "content": "new topic"},
                {"role": "assistant", "content": "C" * 50},
            ],
        )

        boundary = consolidator.pick_consolidation_boundary(session, tokens_to_remove=10)

        self.assertEqual(boundary[0], 2)
        self.assertGreater(boundary[1], 10)

    def test_maybe_consolidate_advances_only_after_success(self) -> None:
        sessions = FakeSessions()
        store = MemoryStore(str(self.temp_dir))
        provider = FakeProvider(
            [
                FakeResponse(
                    tool_calls=[
                        FakeToolCall(
                            arguments={"history_entry": "[2026-03-17 10:00] archived part 1", "memory_update": "memory v1"}
                        )
                    ],
                    has_tool_calls=True,
                ),
                FakeResponse(tool_calls=[], has_tool_calls=False),
            ]
        )
        consolidator = MemoryConsolidator(
            sessions,
            store,
            provider=provider,
            model="gpt-5.4",
            context_window_tokens=20,
        )
        session = Session(
            key="cli:tokens",
            messages=[
                {"role": "assistant", "content": "A" * 60, "timestamp": "2026-03-17T10:00:00Z"},
                {"role": "assistant", "content": "B" * 60, "timestamp": "2026-03-17T10:01:00Z"},
                {"role": "user", "content": "topic one", "timestamp": "2026-03-17T10:02:00Z"},
                {"role": "assistant", "content": "C" * 60, "timestamp": "2026-03-17T10:03:00Z"},
                {"role": "assistant", "content": "D" * 60, "timestamp": "2026-03-17T10:04:00Z"},
                {"role": "user", "content": "topic two", "timestamp": "2026-03-17T10:05:00Z"},
                {"role": "assistant", "content": "E" * 60, "timestamp": "2026-03-17T10:06:00Z"},
            ],
        )

        consolidator.maybe_consolidate_by_tokens(session)

        self.assertEqual(session.last_consolidated, 2)
        self.assertEqual(sessions.saved_watermarks, [2])
        self.assertEqual(store.read_long_term(), "memory v1")
        self.assertIn("archived part 1", store.history_file.read_text(encoding="utf-8"))

    def test_archive_unconsolidated_does_not_advance_watermark(self) -> None:
        sessions = FakeSessions()
        store = MemoryStore(str(self.temp_dir))
        provider = FakeProvider(
            [
                FakeResponse(
                    tool_calls=[
                        FakeToolCall(
                            arguments={"history_entry": "[2026-03-17 10:00] archived", "memory_update": "memory v2"}
                        )
                    ],
                    has_tool_calls=True,
                )
            ]
        )
        consolidator = MemoryConsolidator(sessions, store, provider=provider, model="gpt-5.4")
        session = Session(
            key="cli:archive",
            messages=[
                {"role": "assistant", "content": "old", "timestamp": "2026-03-17T10:00:00Z"},
                {"role": "user", "content": "keep", "timestamp": "2026-03-17T10:01:00Z"},
            ],
            last_consolidated=1,
        )

        ok = consolidator.archive_unconsolidated(session)

        self.assertTrue(ok)
        self.assertEqual(session.last_consolidated, 1)
        self.assertEqual(sessions.saved_watermarks, [])

    def test_get_lock_reuses_same_session_key(self) -> None:
        consolidator = MemoryConsolidator(FakeSessions(), MemoryStore(str(self.temp_dir)))

        first = consolidator.get_lock("cli:same")
        second = consolidator.get_lock("cli:same")
        third = consolidator.get_lock("cli:other")

        self.assertIs(first, second)
        self.assertIsNot(first, third)


if __name__ == "__main__":
    unittest.main()
