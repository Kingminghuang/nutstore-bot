from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_paths import discovery_file_path as resolved_discovery_file_path
from local_paths import ensure_nsbot_root, nsbot_home


@dataclass(frozen=True)
class ServiceDiscovery:
    base_url: str
    port: int
    token: str
    pid: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseUrl": self.base_url,
            "port": self.port,
            "token": self.token,
            "pid": self.pid,
        }


def discovery_file_path(ns_bot_home: str | None = None) -> Path:
    return resolved_discovery_file_path(ns_bot_home)


def write_service_discovery(
    discovery: ServiceDiscovery, ns_bot_home: str | None = None
) -> Path:
    root = ensure_nsbot_root(ns_bot_home)

    destination = discovery_file_path(str(root))
    temp_path = destination.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(discovery.to_dict(), ensure_ascii=True), encoding="utf-8"
    )
    temp_path.replace(destination)
    return destination


def read_service_discovery(ns_bot_home: str | None = None) -> ServiceDiscovery:
    source = discovery_file_path(ns_bot_home)
    payload = json.loads(source.read_text(encoding="utf-8"))

    return ServiceDiscovery(
        base_url=str(payload["baseUrl"]),
        port=int(payload["port"]),
        token=str(payload["token"]),
        pid=int(payload["pid"]),
    )
