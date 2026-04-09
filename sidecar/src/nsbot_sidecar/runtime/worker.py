from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nsbot_sidecar.infrastructure.local_paths import nsbot_home
from nsbot_sidecar.runtime.runtime_service import (
    CodeAgentRuntimeService,
    RunMetadata,
    RuntimeProcessError,
    RuntimeWorkerConfig,
)


@dataclass(frozen=True)
class RuntimeRequest:
    run_id: str
    user_input: str
    auth_context: dict[str, Any]
    metadata: RunMetadata
    config: RuntimeWorkerConfig


@dataclass(frozen=True)
class RuntimeResponse:
    success: bool
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None

    def to_json(self) -> str:
        payload = {
            "success": self.success,
            "result": self.result,
            "error": self.error,
        }
        return json.dumps(payload, ensure_ascii=False)


def _pick(data: dict[str, Any], snake_key: str, camel_key: str) -> Any:
    if snake_key in data:
        return data[snake_key]
    return data.get(camel_key)


def _default_workspace_path() -> str:
    try:
        return str(Path.home())
    except RuntimeError:
        return os.getcwd()


def parse_request(raw: str) -> RuntimeRequest:
    data = json.loads(raw)
    metadata_data = data.get("metadata") or {}
    auth_context_data = data.get("auth_context") or data.get("authContext") or {}
    config_data = data.get("config") or {}

    return RuntimeRequest(
        run_id=str(data.get("run_id") or data.get("runId") or ""),
        user_input=str(data.get("user_input") or data.get("userInput") or ""),
        auth_context={
            "uid": _pick(auth_context_data, "uid", "uid"),
            "tid": _pick(auth_context_data, "tid", "tid"),
            "exp_epoch": _pick(auth_context_data, "exp_epoch", "expEpoch"),
        },
        metadata=RunMetadata(
            workspace_path=_pick(metadata_data, "workspace_path", "workspacePath"),
            session_key=_pick(metadata_data, "session_key", "sessionKey"),
        ),
        config=RuntimeWorkerConfig(
            model_id=str(_pick(config_data, "model_id", "modelId") or "gpt-5.4"),
            provider=str(
                _pick(config_data, "provider", "providerId")
                or _pick(config_data, "provider", "provider")
                or ""
            ).strip()
            or None,
            base_url=str(
                _pick(config_data, "base_url", "baseUrl")
                or _pick(config_data, "base_url", "baseUrl")
                or ""
            ).strip()
            or None,
            api_key=str(
                _pick(config_data, "api_key", "apiKey")
                or _pick(config_data, "api_key", "apiKey")
                or ""
            ).strip()
            or None,
            model=str(
                _pick(config_data, "model", "selectedModelId")
                or _pick(config_data, "model", "model")
                or ""
            ).strip()
            or None,
            request_timeout_ms=int(
                _pick(config_data, "request_timeout_ms", "requestTimeoutMs") or 60_000
            ),
            ns_bot_home=str(
                _pick(config_data, "ns_bot_home", "nsBotHome") or nsbot_home()
            ),
            workspace_path_default=str(
                _pick(config_data, "workspace_path_default", "workspacePathDefault")
                or _default_workspace_path()
            ),
            fd_executable=str(
                _pick(config_data, "fd_executable", "fdExecutable") or ""
            ).strip()
            or None,
            rg_executable=str(
                _pick(config_data, "rg_executable", "rgExecutable") or ""
            ).strip()
            or None,
            tool_os_type=str(
                _pick(config_data, "tool_os_type", "toolOsType") or ""
            ).strip()
            or None,
            max_steps=int(_pick(config_data, "max_steps", "maxSteps") or 20),
        ),
    )


def main() -> int:
    raw = sys.stdin.readline()
    if raw.strip() == "":
        response = RuntimeResponse(
            success=False,
            error={
                "code": "runtime_bridge_error",
                "message": "empty request",
            },
        )
        print(response.to_json(), flush=True)
        return 1

    try:
        request = parse_request(raw)
        service = CodeAgentRuntimeService(request.config)
        result = service.process(
            run_id=request.run_id,
            user_input=request.user_input,
            auth_context=request.auth_context,
            metadata=request.metadata,
        )
        print(RuntimeResponse(success=True, result=result).to_json(), flush=True)
        return 0
    except RuntimeProcessError as exc:
        print(
            RuntimeResponse(
                success=False,
                error={
                    "code": exc.code,
                    "message": exc.message,
                },
            ).to_json(),
            flush=True,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(
            RuntimeResponse(
                success=False,
                error={
                    "code": "runtime_error",
                    "message": str(exc),
                },
            ).to_json(),
            flush=True,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
