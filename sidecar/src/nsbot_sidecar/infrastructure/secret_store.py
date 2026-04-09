from __future__ import annotations

import json
import re
from dataclasses import dataclass

from nsbot_sidecar.infrastructure.local_paths import ensure_secret_dir, secrets_dir_path


@dataclass(frozen=True)
class ProviderSecretPayload:
    version: int
    api_key: str | None
    secret_headers: dict[str, str]


class LocalSecretStore:
    def __init__(self, ns_bot_home: str | None = None):
        self._ns_bot_home = ns_bot_home
        ensure_secret_dir(ns_bot_home)

    def bootstrap_master_key(self) -> str:
        # Plaintext mode: keep this method for API compatibility.
        return "plaintext-mode: no master key file"

    def has_secret(self, secret_ref: str) -> bool:
        return self._secret_file_path(secret_ref).exists()

    def save_provider_secret(
        self, secret_ref: str, payload: ProviderSecretPayload
    ) -> str:
        plain_payload = json.dumps(
            {
                "version": payload.version,
                "apiKey": payload.api_key,
                "secretHeaders": payload.secret_headers,
            },
            ensure_ascii=True,
        )

        destination = self._secret_file_path(secret_ref)
        temp_path = destination.with_suffix(".enc.tmp")
        temp_path.write_text(plain_payload, encoding="utf-8")
        temp_path.replace(destination)
        return str(destination)

    def load_provider_secret(self, secret_ref: str) -> ProviderSecretPayload | None:
        source = self._secret_file_path(secret_ref)
        if not source.exists():
            return None

        payload = json.loads(source.read_text(encoding="utf-8"))

        return ProviderSecretPayload(
            version=int(payload.get("version", 1)),
            api_key=str(payload["apiKey"]) if payload.get("apiKey") is not None else None,
            secret_headers=_normalize_secret_headers(payload.get("secretHeaders")),
        )

    def delete_provider_secret(self, secret_ref: str) -> None:
        target = self._secret_file_path(secret_ref)
        if target.exists():
            target.unlink()

    def _secret_file_path(self, secret_ref: str):
        safe_ref = re.sub(r"[^A-Za-z0-9._-]", "_", secret_ref.strip())
        if safe_ref == "":
            raise ValueError("Secret ref cannot be empty")
        return secrets_dir_path(self._ns_bot_home) / f"{safe_ref}.enc"


def _normalize_secret_headers(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    result: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, str):
            result[key] = item
    return result
