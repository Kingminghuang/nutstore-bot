from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from local_paths import ensure_secret_dir, master_key_path, secrets_dir_path


KEY_BYTES = 32


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
        self._load_or_create_master_key()
        return str(master_key_path(self._ns_bot_home))

    def has_secret(self, secret_ref: str) -> bool:
        return self._secret_file_path(secret_ref).exists()

    def save_provider_secret(
        self, secret_ref: str, payload: ProviderSecretPayload
    ) -> str:
        key = self._load_or_create_master_key()
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        plaintext = json.dumps(
            {
                "version": payload.version,
                "apiKey": payload.api_key,
                "secretHeaders": payload.secret_headers,
            },
            ensure_ascii=True,
        ).encode("utf-8")
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        envelope = {
            "version": 1,
            "cipher": "aes-256-gcm",
            "nonce": nonce.hex(),
            "ciphertext": ciphertext.hex(),
        }

        destination = self._secret_file_path(secret_ref)
        temp_path = destination.with_suffix(".enc.tmp")
        temp_path.write_text(json.dumps(envelope, ensure_ascii=True), encoding="utf-8")
        temp_path.replace(destination)
        return str(destination)

    def load_provider_secret(self, secret_ref: str) -> ProviderSecretPayload | None:
        source = self._secret_file_path(secret_ref)
        if not source.exists():
            return None

        payload = json.loads(source.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or payload.get("cipher") != "aes-256-gcm":
            raise ValueError(f"Invalid secret envelope for {secret_ref}")

        key = self._load_or_create_master_key()
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(
            bytes.fromhex(str(payload["nonce"])),
            bytes.fromhex(str(payload["ciphertext"])),
            None,
        )
        decrypted = json.loads(plaintext.decode("utf-8"))

        return ProviderSecretPayload(
            version=1,
            api_key=str(decrypted["apiKey"])
            if decrypted.get("apiKey") is not None
            else None,
            secret_headers=_normalize_secret_headers(decrypted.get("secretHeaders")),
        )

    def delete_provider_secret(self, secret_ref: str) -> None:
        target = self._secret_file_path(secret_ref)
        if target.exists():
            target.unlink()

    def _load_or_create_master_key(self) -> bytes:
        destination = master_key_path(self._ns_bot_home)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            data = destination.read_bytes()
            if len(data) != KEY_BYTES:
                raise ValueError(f"Invalid master key length at {destination}")
            return data

        key = AESGCM.generate_key(bit_length=256)
        destination.write_bytes(key)
        return key

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
