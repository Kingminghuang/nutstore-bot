from __future__ import annotations

import shutil
from pathlib import Path

from local_paths import ensure_nsbot_root


class AttachmentStore:
    def __init__(self, ns_bot_home: str | None = None):
        root = ensure_nsbot_root(ns_bot_home)
        self.attachments_dir = root / "attachments"
        self.attachments_dir.mkdir(parents=True, exist_ok=True)

    def relative_path(self, attachment_id: str, file_name: str) -> str:
        safe_name = file_name.replace("/", "_").replace("\\", "_")
        return f"{attachment_id}/{safe_name}"

    def draft_relative_path(self, draft_attachment_id: str, file_name: str) -> str:
        safe_name = file_name.replace("/", "_").replace("\\", "_")
        return f"drafts/{draft_attachment_id}/{safe_name}"

    def absolute_path(self, relative_path: str) -> Path:
        return (self.attachments_dir / relative_path).resolve()

    def write_bytes(self, relative_path: str, payload: bytes) -> Path:
        destination = self.absolute_path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return destination

    def delete_file(self, relative_path: str) -> None:
        target = self.absolute_path(relative_path)
        if target.exists() and target.is_file():
            target.unlink()

    def move_file(self, src_relative_path: str, dest_relative_path: str) -> Path:
        source = self.absolute_path(src_relative_path)
        destination = self.absolute_path(dest_relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return destination
