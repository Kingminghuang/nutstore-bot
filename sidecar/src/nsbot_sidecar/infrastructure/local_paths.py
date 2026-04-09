from __future__ import annotations

import os
import sys
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


def discovery_file_path(ns_bot_home_override: str | None = None) -> Path:
    return nsbot_home(ns_bot_home_override) / "service.json"


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
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            # Some callers provide escaped Windows paths (e.g. C:\\Users\\...).
            appdata = appdata.replace("\\\\", "\\")
            return Path(appdata) / APP_NAME
        return None

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / APP_NAME

    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / APP_NAME

    return None
