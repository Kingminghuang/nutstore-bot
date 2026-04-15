from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
ALLOWED_ROLES = {"user", "assistant", "system", "tool-call", "tool-response"}


def now_iso8601() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def safe_session_key(key: str) -> str:
    normalized = key.replace(":", "_")
    normalized = re.sub(r'[<>:"/\\|?*]', "_", normalized).strip()
    if normalized == "":
        normalized = "default"
    return normalized


@dataclass
class Session:
    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso8601)
    updated_at: str = field(default_factory=now_iso8601)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0

    def add_message(self, role: str, content: Any, **kwargs: Any) -> None:
        normalized_role = str(role).strip()
        if normalized_role not in ALLOWED_ROLES:
            raise ValueError(f"Unsupported session role: {normalized_role}")
        msg = {
            "role": normalized_role,
            "content": content,
            "timestamp": now_iso8601(),
            "run_id": kwargs.pop("run_id", None),
            "step_id": kwargs.pop("step_id", None),
            "source_kind": kwargs.pop("source_kind", None),
            "internal": bool(kwargs.pop("internal", False)),
            **kwargs,
        }
        self.messages.append(msg)
        self.updated_at = now_iso8601()

    def append_messages(self, messages: list[dict[str, Any]]) -> None:
        for message in messages:
            self.add_message(
                str(message.get("role") or "assistant"),
                message.get("content", ""),
                **{
                    key: value
                    for key, value in message.items()
                    if key not in {"role", "content", "timestamp"}
                },
            )

    def truncate_by_run_id(self, run_id: str) -> None:
        cutoff = None
        for idx, message in enumerate(self.messages):
            if str(message.get("run_id") or "") == run_id:
                cutoff = idx
                break
        if cutoff is None:
            return
        self.messages = self.messages[:cutoff]
        self.last_consolidated = min(self.last_consolidated, len(self.messages))
        self.updated_at = now_iso8601()

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        unconsolidated = self.messages[self.last_consolidated :]
        if max_messages > 0:
            sliced = unconsolidated[-max_messages:]
        else:
            sliced = list(unconsolidated)

        for idx, msg in enumerate(sliced):
            if msg.get("role") == "user":
                sliced = sliced[idx:]
                break

        history: list[dict[str, Any]] = []
        for msg in sliced:
            entry = {
                "role": msg.get("role", "assistant"),
                "content": msg.get("content", ""),
            }
            for key in [
                "tool_calls",
                "tool_call_id",
                "name",
                "run_id",
                "step_id",
                "source_kind",
                "internal",
            ]:
                if key in msg:
                    entry[key] = msg[key]
            history.append(entry)

        return history

    def clear(self) -> None:
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = now_iso8601()


class SessionManager:
    def __init__(self, ns_bot_home: str):
        self.ns_bot_home = Path(ns_bot_home).expanduser().resolve()
        self.sessions_dir = self.ns_bot_home / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.cache: dict[str, Session] = {}

    def session_path(self, key: str) -> Path:
        return self.sessions_dir / f"{safe_session_key(key)}.jsonl"

    def get_or_create(self, key: str) -> Session:
        if key in self.cache:
            return self.cache[key]

        loaded = self.load(key)
        if loaded is None:
            loaded = Session(key=key)

        self.cache[key] = loaded
        return loaded

    def load(self, key: str) -> Session | None:
        path = self.session_path(key)

        if not path.exists():
            return None

        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: str | None = None
            updated_at: str | None = None
            last_consolidated = 0

            with path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    line = raw.strip()
                    if line == "":
                        continue
                    data = json.loads(line)
                    if data.get("_type") == "metadata":
                        raw_metadata = data.get("metadata", {})
                        metadata = (
                            raw_metadata if isinstance(raw_metadata, dict) else {}
                        )
                        created_at = data.get("created_at") or created_at
                        updated_at = data.get("updated_at") or updated_at
                        last_consolidated = int(data.get("last_consolidated", 0))
                    else:
                        messages.append(data)

            session = Session(
                key=key,
                messages=messages,
                created_at=created_at or now_iso8601(),
                updated_at=updated_at or now_iso8601(),
                metadata=metadata,
                last_consolidated=last_consolidated,
            )
            return session
        except Exception:
            LOGGER.warning(
                "Failed to load session",
                extra={"session_key": key, "path": str(path)},
                exc_info=True,
            )
            return None

    def save(self, session: Session) -> None:
        path = self.session_path(session.key)
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as handle:
            metadata = {
                "_type": "metadata",
                "key": session.key,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "metadata": session.metadata,
                "last_consolidated": session.last_consolidated,
            }
            handle.write(json.dumps(metadata, ensure_ascii=False) + "\n")
            for msg in session.messages:
                handle.write(json.dumps(msg, ensure_ascii=False) + "\n")

        self.cache[session.key] = session

    def delete(self, key: str) -> None:
        path = self.session_path(key)
        self.invalidate(key)

        if not path.exists():
            return
        if not path.is_file():
            LOGGER.warning(
                "Session path is not a file; skipping delete",
                extra={"session_key": key, "path": str(path)},
            )
            return

        path.unlink()

    def invalidate(self, key: str) -> None:
        self.cache.pop(key, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self.sessions_dir.glob("*.jsonl")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    first_line = handle.readline().strip()
                if first_line == "":
                    continue
                data = json.loads(first_line)
                if data.get("_type") != "metadata":
                    continue
                session_key = str(data.get("key") or path.stem)
                raw_metadata = data.get("metadata", {})
                metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
                out.append(
                    {
                        "session_key": session_key,
                        "key": session_key,
                        "created_at": data.get("created_at", ""),
                        "updated_at": data.get("updated_at", ""),
                        "metadata": metadata,
                        "path": str(path),
                    }
                )
            except Exception:
                continue

        out.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return out
