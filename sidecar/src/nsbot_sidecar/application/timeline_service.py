from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from nsbot_sidecar.infrastructure.repositories import AcpEventLogRepository, SessionsRepository
from nsbot_sidecar.domain.session_titles import (
    build_first_user_message_fallback_title,
    build_heuristic_title,
)


@dataclass(frozen=True)
class TimelineService:
    sessions: SessionsRepository
    acp_event_log: AcpEventLogRepository

    def list_timeline_payload(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        before_sequence: int | None = None,
    ) -> dict[str, Any]:
        self._get_session_or_404(session_id)

        if limit is None:
            events = self.acp_event_log.list_by_session_id(session_id)
            return {
                "events": [serialize_timeline_event(event) for event in events],
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

        page, has_more, next_before_sequence = self.acp_event_log.list_by_session_id_page(
            session_id,
            limit=limit,
            before_sequence=before_sequence,
        )
        return {
            "events": [serialize_timeline_event(event) for event in page],
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
        events = self.acp_event_log.list_by_session_id(session_id)
        transcript_messages = _extract_transcript_messages(events)
        last_message = transcript_messages[-1] if transcript_messages else None

        updated = self.sessions.touch(
            session_id,
            message_count=len(transcript_messages),
            last_message_preview=(last_message or "")[:280] if last_message else None,
            last_message_at=events[-1].created_at if events else None,
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

        user_messages = _extract_messages_by_update(
            self.acp_event_log.list_by_session_id(session_id), "user_message_chunk"
        )
        agent_messages = _extract_messages_by_update(
            self.acp_event_log.list_by_session_id(session_id), "agent_message_chunk"
        )
        first_user = user_messages[0] if user_messages else None
        first_answer = agent_messages[0] if agent_messages else None

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


def serialize_timeline_event(event) -> dict[str, Any]:
    payload: dict[str, Any] | None = None
    try:
        parsed = json.loads(event.event_json)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        payload = None
    return {
        "eventId": event.id,
        "sessionId": event.session_id,
        "turnId": event.turn_id,
        "sequenceNo": event.sequence_no,
        "eventType": event.event_type,
        "payload": payload,
        "createdAt": event.created_at,
    }


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


def _extract_messages_by_update(events: list[Any], target_update: str) -> list[str]:
    messages: list[str] = []
    for event in events:
        parsed = _parse_event_payload(event.event_json)
        if not parsed:
            continue
        update = (
            parsed.get("params", {}).get("update")
            if isinstance(parsed.get("params"), dict)
            else None
        )
        if not isinstance(update, dict):
            continue
        if str(update.get("sessionUpdate") or "") != target_update:
            continue
        content = update.get("content")
        if isinstance(content, dict) and str(content.get("type") or "") == "text":
            if target_update == "user_message_chunk":
                text = str(
                    content.get("displayText")
                    or content.get("editableText")
                    or content.get("text")
                    or ""
                ).strip()
            else:
                text = str(content.get("text") or "").strip()
            if text:
                messages.append(text)
    return messages


def _extract_transcript_messages(events: list[Any]) -> list[str]:
    return _extract_messages_by_update(events, "user_message_chunk") + _extract_messages_by_update(
        events, "agent_message_chunk"
    )


def _parse_event_payload(event_json: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(event_json)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
