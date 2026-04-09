from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from nsbot_sidecar.infrastructure.local_paths import ensure_nsbot_root, nsbot_home

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18765
CONFIG_FILE_NAME = "sidecar-client.json"
DEFAULT_AUTH_TYPE = "bearer"


@dataclass(frozen=True)
class SidecarClientConfig:
    base_url: str
    auth_type: str
    token_or_password: str

    @property
    def auth_header_value(self) -> str:
        if self.auth_type == "basic":
            encoded = base64.b64encode(f"nsbot:{self.token_or_password}".encode("utf-8")).decode("ascii")
            return f"Basic {encoded}"
        return f"Bearer {self.token_or_password}"

    def to_dict(self) -> dict[str, str]:
        return {
            "baseUrl": self.base_url,
            "authType": self.auth_type,
            "tokenOrPassword": self.token_or_password,
            "authHeaderValue": self.auth_header_value,
        }


def client_config_path(ns_bot_home_override: str | None = None) -> Path:
    return nsbot_home(ns_bot_home_override) / CONFIG_FILE_NAME


def _normalize_base_url(base_url: str) -> str:
    candidate = base_url.strip().rstrip("/")
    if candidate == "":
        raise ValueError("baseUrl must not be empty")
    return candidate


def _normalize_auth_type(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"bearer", "basic"}:
        raise ValueError("authType must be 'bearer' or 'basic'")
    return normalized


def _normalize_secret(value: str) -> str:
    secret = value.strip()
    if secret == "":
        raise ValueError("tokenOrPassword must not be empty")
    return secret


def _default_config(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> SidecarClientConfig:
    return SidecarClientConfig(
        base_url=f"http://{host}:{port}",
        auth_type=DEFAULT_AUTH_TYPE,
        token_or_password=secrets.token_urlsafe(32),
    )


def load_or_create_client_config(
    ns_bot_home_override: str | None = None,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> SidecarClientConfig:
    root = ensure_nsbot_root(ns_bot_home_override)
    destination = client_config_path(str(root))

    if destination.exists():
        payload = json.loads(destination.read_text(encoding="utf-8"))
        config = SidecarClientConfig(
            base_url=_normalize_base_url(str(payload["baseUrl"])),
            auth_type=_normalize_auth_type(str(payload["authType"])),
            token_or_password=_normalize_secret(str(payload["tokenOrPassword"])),
        )
        return config

    config = _default_config(host=host, port=port)
    write_client_config(config, str(root))
    return config


def write_client_config(
    config: SidecarClientConfig,
    ns_bot_home_override: str | None = None,
) -> Path:
    root = ensure_nsbot_root(ns_bot_home_override)
    destination = client_config_path(str(root))
    temp_path = destination.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(config.to_dict(), ensure_ascii=True),
        encoding="utf-8",
    )
    temp_path.replace(destination)
    return destination
