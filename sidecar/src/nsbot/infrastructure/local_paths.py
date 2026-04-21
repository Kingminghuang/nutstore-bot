from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "NutstoreBot"
LEGACY_ROOT_NAME = ".nsbot"


def nsbot_home(ns_bot_home: str | None = None) -> Path:
    if ns_bot_home:
        return Path(ns_bot_home).expanduser().resolve()

    env_value = os.environ.get("NS_BOT_HOME")
    if env_value:
        return Path(env_value).expanduser().resolve()

    platform_root = _platform_storage_root()
    if platform_root is not None:
        return platform_root.resolve()

    return (Path.home() / LEGACY_ROOT_NAME).resolve()


def database_file_path(ns_bot_home_override: str | None = None) -> Path:
    return nsbot_home(ns_bot_home_override) / "sidecar.db"


def secrets_dir_path(ns_bot_home_override: str | None = None) -> Path:
    return nsbot_home(ns_bot_home_override) / "secrets"


def master_key_path(ns_bot_home_override: str | None = None) -> Path:
    return nsbot_home(ns_bot_home_override) / "master.key"


def ensure_nsbot_root(ns_bot_home_override: str | None = None) -> Path:
    root = nsbot_home(ns_bot_home_override)
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_secret_dir(ns_bot_home_override: str | None = None) -> Path:
    secrets_dir = secrets_dir_path(ns_bot_home_override)
    secrets_dir.mkdir(parents=True, exist_ok=True)
    return secrets_dir


def _platform_storage_root() -> Path | None:
    return Path.home() / APP_NAME
