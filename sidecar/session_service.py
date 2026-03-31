from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
from typing import Any, Callable, Protocol

from fastapi import BackgroundTasks, HTTPException, status

from attachment_store import AttachmentStore
from redaction import redact_text
from repositories import (
    AttachmentsRepository,
    DraftAttachmentsRepository,
    SessionsRepository,
    WorkspacesRepository,
    create_id,
)
from session_manager import SessionManager
from session_titles import (
    build_first_user_message_fallback_title,
    build_heuristic_title,
)
from timeline_service import TimelineService, serialize_session_summary


ModelTitleGenerator = Callable[[str, str], str | None]


LOGGER = logging.getLogger(__name__)


class WorkspaceSidecarIndexerProtocol(Protocol):
    def enqueue(
        self,
        background_tasks: BackgroundTasks | None,
        workspace_id: str,
        workspace_real_path: str,
    ) -> None: ...

    def status(
        self,
        workspace_id: str,
        workspace_real_path: str,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SessionService:
    workspaces: WorkspacesRepository
    sessions: SessionsRepository
    attachments: AttachmentsRepository
    draft_attachments: DraftAttachmentsRepository
    attachment_store: AttachmentStore
    timeline_service: TimelineService
    model_title_generator: ModelTitleGenerator | None = None
    workspace_sidecar_indexer: WorkspaceSidecarIndexerProtocol | None = None

    def list_workspaces_payload(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "workspaces": [
                serialize_workspace(workspace) for workspace in self.workspaces.list()
            ]
        }

    def create_workspace(
        self,
        payload: dict[str, Any],
        *,
        background_tasks: BackgroundTasks | None = None,
    ) -> dict[str, Any]:
        name = _normalize_required_string(
            payload.get("name"), detail="Directory name is required"
        )
        real_path = _normalize_required_string(
            payload.get("realPath", payload.get("real_path")),
            detail="Directory path is required",
        )
        path_label = _normalize_required_string(
            payload.get("pathLabel", payload.get("path_label", real_path)),
            detail="Directory path label is required",
        )

        resolved_path = Path(real_path).expanduser().resolve()
        if not resolved_path.exists() or not resolved_path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Directory path must point to an existing directory",
            )

        try:
            workspace = self.workspaces.create(
                name=name,
                path_label=path_label,
                real_path=str(resolved_path),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Directory path is already registered",
            ) from exc

        if self.workspace_sidecar_indexer is not None:
            self.workspace_sidecar_indexer.enqueue(
                background_tasks,
                workspace_id=workspace.id,
                workspace_real_path=str(resolved_path),
            )

        return serialize_workspace(workspace)

    def delete_workspace(self, workspace_id: str) -> None:
        self._get_workspace_or_404(workspace_id)
        self.workspaces.delete_by_id(workspace_id)

    def workspace_sidecar_index_status_payload(
        self, workspace_id: str
    ) -> dict[str, Any]:
        workspace = self._get_workspace_or_404(workspace_id)
        if self.workspace_sidecar_indexer is None:
            workspace_path = Path(workspace.real_path).expanduser().resolve()
            sidecar_root = workspace_path / ".sidecar"
            return {
                "workspaceId": workspace.id,
                "workspacePath": str(workspace_path),
                "sidecarRoot": str(sidecar_root),
                "manifestPath": str(sidecar_root / ".index-manifest.json"),
                "manifestExists": False,
                "status": "disabled",
                "lastIndexedAt": None,
                "stats": {
                    "scanned": 0,
                    "converted": 0,
                    "skipped": 0,
                    "failed": 0,
                },
                "sourceCount": 0,
            }
        return self.workspace_sidecar_indexer.status(workspace.id, workspace.real_path)

    def update_workspace(
        self, workspace_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        existing = self._get_workspace_or_404(workspace_id)
        name = _normalize_optional_string(payload.get("name"))
        path_label = _normalize_optional_string(
            payload.get("pathLabel", payload.get("path_label"))
        )
        real_path = _normalize_optional_string(
            payload.get("realPath", payload.get("real_path"))
        )

        resolved_real_path = existing.real_path
        if real_path is not None:
            resolved = Path(real_path).expanduser().resolve()
            if not resolved.exists() or not resolved.is_dir():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Directory path must point to an existing directory",
                )
            resolved_real_path = str(resolved)

        try:
            workspace = self.workspaces.update(
                workspace_id,
                name=name,
                path_label=path_label,
                real_path=resolved_real_path if real_path is not None else None,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Directory path is already registered",
            ) from exc

        return serialize_workspace(workspace)

    def list_sessions_payload(
        self, workspace_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        self._get_workspace_or_404(workspace_id)
        sessions = self.sessions.list_by_workspace_id(workspace_id)
        return {"sessions": [serialize_session(session) for session in sessions]}

    def create_session(
        self, workspace_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._get_workspace_or_404(workspace_id)
        connection_id = _normalize_optional_string(
            payload.get("connectionId", payload.get("connection_id"))
        )
        model_id = _normalize_optional_string(
            payload.get("modelId", payload.get("model_id"))
        )

        session = self.sessions.create(
            workspace_id=workspace_id,
            active_connection_id=connection_id,
            active_model_id=model_id,
        )
        return serialize_session(session)

    def delete_session(self, session_id: str) -> None:
        session = self._get_session_or_404(session_id)
        try:
            runtime_sessions = SessionManager(
                str(self.attachment_store.attachments_dir.parent)
            )
            runtime_sessions.delete(session.session_key)
        except Exception:
            LOGGER.exception(
                "Failed to remove persisted session file",
                extra={"session_id": session.id, "session_key": session.session_key},
            )
        self.sessions.delete_by_id(session.id)

    def update_session(
        self, session_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        session = self._get_session_or_404(session_id)
        title = _normalize_required_string(
            payload.get("title"), detail="Session title is required"
        )

        updated = self.sessions.touch(
            session.id,
            title=title,
            title_source="manual",
            title_status="idle",
        )
        LOGGER.info(
            "Session renamed: session_id=%s workspace_id=%s title=%s",
            updated.id,
            updated.workspace_id,
            updated.title,
        )
        return serialize_session(updated)

    def list_timeline_payload(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        before_sequence: int | None = None,
    ) -> dict[str, Any]:
        return self.timeline_service.list_timeline_payload(
            session_id,
            limit=limit,
            before_sequence=before_sequence,
        )

    def list_attachments_payload(
        self, session_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        session = self._get_session_or_404(session_id)
        records = self.attachments.list_by_session_id(session.id, statuses=["uploaded"])
        next_records = []
        for record in records:
            file_path = self.attachment_store.absolute_path(record.storage_path)
            if not file_path.exists():
                self.attachments.update_status(record.id, "missing")
                continue
            next_records.append(record)
        return {
            "attachments": [serialize_attachment(record) for record in next_records]
        }

    def create_attachment(
        self,
        session_id: str,
        *,
        file_name: str,
        mime_type: str,
        payload: bytes,
    ) -> dict[str, Any]:
        session = self._get_session_or_404(session_id)
        workspace = self._get_workspace_or_404(session.workspace_id)
        if len(payload) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Attachment file is empty",
            )

        normalized_name = _normalize_required_string(
            file_name, detail="Attachment file name is required"
        )
        attachment_id = create_id("att")
        relative_path = self.attachment_store.relative_path(
            attachment_id, normalized_name
        )
        self.attachment_store.write_bytes(relative_path, payload)

        record = self.attachments.create(
            attachment_id=attachment_id,
            session_id=session.id,
            workspace_id=workspace.id,
            file_name=normalized_name,
            mime_type=_normalize_optional_string(mime_type)
            or "application/octet-stream",
            size_bytes=len(payload),
            storage_path=relative_path,
            status="uploaded",
        )
        return serialize_attachment(record)

    def list_draft_attachments_payload(
        self, workspace_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        workspace = self._get_workspace_or_404(workspace_id)
        records = self.draft_attachments.list_by_workspace_id(workspace.id)
        next_records = []
        for record in records:
            file_path = self.attachment_store.absolute_path(record.storage_path)
            if not file_path.exists():
                self.draft_attachments.delete_by_id(record.id)
                continue
            next_records.append(record)
        return {
            "draftAttachments": [
                serialize_draft_attachment(record) for record in next_records
            ]
        }

    def create_draft_attachment(
        self,
        workspace_id: str,
        *,
        file_name: str,
        mime_type: str,
        payload: bytes,
    ) -> dict[str, Any]:
        workspace = self._get_workspace_or_404(workspace_id)
        if len(payload) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Attachment file is empty",
            )

        normalized_name = _normalize_required_string(
            file_name, detail="Attachment file name is required"
        )
        draft_attachment_id = create_id("draftatt")
        relative_path = self.attachment_store.draft_relative_path(
            draft_attachment_id, normalized_name
        )
        self.attachment_store.write_bytes(relative_path, payload)

        record = self.draft_attachments.create(
            draft_attachment_id=draft_attachment_id,
            workspace_id=workspace.id,
            file_name=normalized_name,
            mime_type=_normalize_optional_string(mime_type)
            or "application/octet-stream",
            size_bytes=len(payload),
            storage_path=relative_path,
        )
        return serialize_draft_attachment(record)

    def delete_draft_attachment(
        self, workspace_id: str, draft_attachment_id: str
    ) -> None:
        workspace = self._get_workspace_or_404(workspace_id)
        try:
            record = self.draft_attachments.get_by_id(draft_attachment_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Draft attachment not found",
            ) from exc

        if record.workspace_id != workspace.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Draft attachment not found",
            )

        self.attachment_store.delete_file(record.storage_path)
        self.draft_attachments.delete_by_id(draft_attachment_id)

    def delete_attachment(self, session_id: str, attachment_id: str) -> None:
        session = self._get_session_or_404(session_id)
        try:
            record = self.attachments.get_by_id(attachment_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Attachment not found",
            ) from exc

        if record.session_id != session.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Attachment not found",
            )

        self.attachments.update_status(attachment_id, "deleted")
        self.attachment_store.delete_file(record.storage_path)
        self.attachments.delete_by_id(attachment_id)

    def apply_first_user_message_title(
        self,
        session_id: str,
        text: str,
        *,
        active_connection_id: str | None = None,
        active_model_id: str | None = None,
    ) -> None:
        session = self._get_session_or_404(session_id)
        if session.title_source == "manual" or session.message_count > 0:
            return

        heuristic_title = build_heuristic_title(text)
        self.sessions.touch(
            session_id,
            title=heuristic_title,
            title_source="heuristic",
            active_connection_id=active_connection_id,
            active_model_id=active_model_id,
        )

    def apply_model_generated_title(
        self, session_id: str, title: str
    ) -> dict[str, Any]:
        session = self._get_session_or_404(session_id)
        if session.title_source == "manual":
            return serialize_session_summary(session)

        normalized_title = build_heuristic_title(title)
        updated = self.sessions.touch(
            session_id,
            title=normalized_title,
            title_source="model",
            title_status="ready",
        )
        return serialize_session_summary(updated)

    def generate_model_title(self, session_id: str) -> dict[str, Any]:
        return self.timeline_service.apply_title_from_timeline(session_id)

    def _apply_title_generation_fallback(
        self, session_id: str, first_user_message_content: str
    ) -> dict[str, Any]:
        fallback_title = build_first_user_message_fallback_title(
            first_user_message_content
        )
        updated = self.sessions.touch(
            session_id,
            title=fallback_title,
            title_source="heuristic",
            title_status="failed",
        )
        return serialize_session_summary(updated)

    def _get_workspace_or_404(self, workspace_id: str):
        try:
            return self.workspaces.get_by_id(workspace_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workspace not found",
            ) from exc

    def _get_session_or_404(self, session_id: str):
        try:
            return self.sessions.get_by_id(session_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            ) from exc


def serialize_workspace(workspace) -> dict[str, Any]:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "pathLabel": workspace.path_label,
        "realPath": workspace.real_path,
        "createdAt": workspace.created_at,
        "updatedAt": workspace.updated_at,
    }


def serialize_session(session) -> dict[str, Any]:
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


def serialize_attachment(attachment) -> dict[str, Any]:
    return {
        "id": attachment.id,
        "sessionId": attachment.session_id,
        "workspaceId": attachment.workspace_id,
        "fileName": attachment.file_name,
        "mimeType": attachment.mime_type,
        "sizeBytes": attachment.size_bytes,
        "status": attachment.status,
        "createdAt": attachment.created_at,
        "updatedAt": attachment.updated_at,
    }


def serialize_draft_attachment(draft_attachment) -> dict[str, Any]:
    return {
        "id": draft_attachment.id,
        "workspaceId": draft_attachment.workspace_id,
        "fileName": draft_attachment.file_name,
        "mimeType": draft_attachment.mime_type,
        "sizeBytes": draft_attachment.size_bytes,
        "createdAt": draft_attachment.created_at,
        "updatedAt": draft_attachment.updated_at,
    }


def build_model_title(user_text: str, assistant_text: str) -> str:
    candidate_words = _title_words(_strip_title_prefixes(user_text))
    while candidate_words and candidate_words[-1].lower() in _TITLE_TRAILING_FILLERS:
        candidate_words.pop()

    if len(candidate_words) < 4:
        existing_words = {word.lower() for word in candidate_words}
        for word in _title_words(assistant_text):
            lowered = word.lower()
            if lowered in existing_words:
                continue
            candidate_words.append(word)
            existing_words.add(lowered)
            if len(candidate_words) >= 8:
                break

    if not candidate_words:
        return build_heuristic_title(user_text)

    title = " ".join(candidate_words[:8]).strip()
    if not title:
        return build_heuristic_title(user_text)
    title = title[:1].upper() + title[1:]
    if len(title) > 60:
        title = title[:57].rstrip() + "..."
    return title


def _strip_title_prefixes(text: str) -> str:
    normalized = text.strip()
    if not normalized:
        return normalized

    previous = None
    while normalized != previous:
        previous = normalized
        for pattern in _TITLE_PREFIX_PATTERNS:
            normalized = pattern.sub("", normalized, count=1).strip()
    return normalized


def _title_words(text: str) -> list[str]:
    return _TITLE_WORD_PATTERN.findall(text)


def _normalize_required_string(value: Any, *, detail: str) -> str:
    normalized = _normalize_optional_string(value)
    if normalized is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=redact_text(detail),
        )
    return normalized


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_TITLE_PREFIX_PATTERNS = (
    re.compile(r"^(?:can|could|would)\s+you\s+", re.IGNORECASE),
    re.compile(r"^please\s+", re.IGNORECASE),
    re.compile(r"^help\s+me\s+(?:with\s+)?", re.IGNORECASE),
    re.compile(r"^i\s+need\s+to\s+", re.IGNORECASE),
    re.compile(r"^i\s+want\s+to\s+", re.IGNORECASE),
    re.compile(r"^lets?\s+", re.IGNORECASE),
    re.compile(r"^how\s+do\s+i\s+", re.IGNORECASE),
    re.compile(r"^how\s+to\s+", re.IGNORECASE),
)
_TITLE_WORD_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
_TITLE_TRAILING_FILLERS = {"please", "thanks", "thank", "carefully"}
