from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from nsbot_sidecar.infrastructure.repositories import SessionsRepository, TimelineEntriesRepository
from nsbot_sidecar.domain.session_titles import (
    build_first_user_message_fallback_title,
    build_heuristic_title,
)


@dataclass(frozen=True)
class TimelineService:
    sessions: SessionsRepository
    timeline_entries: TimelineEntriesRepository

    def list_timeline_payload(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        before_sequence: int | None = None,
    ) -> dict[str, Any]:
        self._get_session_or_404(session_id)

        if limit is None:
            entries = self.timeline_entries.list_by_session_id(session_id)
            return {
                "entries": [serialize_timeline_entry(entry) for entry in entries],
                "pagination": {"hasMore": False, "nextBeforeSequence": None},
            }

        if limit <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Timeline limit must be greater than 0",
            )
        if before_sequence is not None and before_sequence <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="beforeSequence must be greater than 0",
            )

        page, has_more, next_before_sequence = (
            self.timeline_entries.list_by_session_id_page(
                session_id,
                limit=limit,
                before_sequence=before_sequence,
            )
        )
        return {
            "entries": [serialize_timeline_entry(entry) for entry in page],
            "pagination": {
                "hasMore": has_more,
                "nextBeforeSequence": next_before_sequence,
            },
        }

    def refresh_session_summary(
        self,
        session_id: str,
        *,
        active_connection_id: str | None = None,
        active_model_id: str | None = None,
        trigger_title_generation: bool = False,
    ) -> dict[str, Any]:
        session = self._get_session_or_404(session_id)
        transcript_entries = [
            entry
            for entry in self.timeline_entries.list_by_session_id(session_id)
            if entry.entry_kind in {"user_input", "final_answer", "system_notice"}
        ]
        last_entry = transcript_entries[-1] if transcript_entries else None
        updated = self.sessions.touch(
            session_id,
            message_count=len(transcript_entries),
            last_message_preview=(last_entry.content_text or "")[:280]
            if last_entry
            else None,
            last_message_at=last_entry.created_at if last_entry else None,
            active_connection_id=active_connection_id or session.active_connection_id,
            active_model_id=active_model_id or session.active_model_id,
            updated_at=None,
        )
        if trigger_title_generation:
            self.apply_title_from_timeline(session_id)
        return serialize_session_summary(updated)

    def apply_title_from_timeline(self, session_id: str) -> dict[str, Any]:
        session = self._get_session_or_404(session_id)
        if session.title_source == "manual":
            return serialize_session_summary(session)

        first_user = None
        first_answer = None
        for entry in self.timeline_entries.list_by_session_id(session_id):
            if (
                first_user is None
                and entry.entry_kind == "user_input"
                and entry.content_text
            ):
                first_user = entry.content_text
                continue
            if (
                first_user is not None
                and first_answer is None
                and entry.entry_kind == "final_answer"
                and entry.content_text
            ):
                first_answer = entry.content_text
                break

        if not first_user:
            updated = self.sessions.touch(session_id, title_status="failed")
            return serialize_session_summary(updated)

        if not first_answer:
            updated = self.sessions.touch(
                session_id,
                title=build_first_user_message_fallback_title(first_user),
                title_source="heuristic",
                title_status="failed",
            )
            return serialize_session_summary(updated)

        title = build_heuristic_title(first_user)
        updated = self.sessions.touch(
            session_id,
            title=title,
            title_source="model",
            title_status="ready",
        )
        return serialize_session_summary(updated)

    def _get_session_or_404(self, session_id: str):
        try:
            return self.sessions.get_by_id(session_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
            ) from exc


def serialize_timeline_entry(entry) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": entry.id,
        "sessionId": entry.session_id,
        "runId": entry.run_id,
        "sequenceNo": entry.sequence_no,
        "entryKind": entry.entry_kind,
        "displayRole": entry.display_role,
        "stepId": entry.step_id,
        "stepNumber": entry.step_number,
        "contentText": entry.content_text,
        "createdAt": entry.created_at,
    }
    if entry.content_json is not None:
        try:
            payload["contentJson"] = json.loads(entry.content_json)
        except json.JSONDecodeError:
            payload["contentJson"] = None
    else:
        payload["contentJson"] = None
    return payload


def serialize_session_summary(session) -> dict[str, Any]:
    return {
        "id": session.id,
        "workspaceId": session.workspace_id,
        "title": session.title,
        "titleSource": session.title_source,
        "createdAt": session.created_at,
        "updatedAt": session.updated_at,
        "lastMessageAt": session.last_message_at,
        "messageCount": session.message_count,
        "lastMessagePreview": session.last_message_preview,
        "activeConnectionId": session.active_connection_id,
        "activeModelId": session.active_model_id,
    }
