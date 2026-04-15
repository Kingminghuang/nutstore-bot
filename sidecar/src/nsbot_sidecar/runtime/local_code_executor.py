from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smolagents.local_python_executor import CodeOutput, LocalPythonExecutor


@dataclass
class LocalCodeExecutor:
    turn_id: str
    workspace_path: str
    timeout_seconds: int = 30
    permission_requester: Any | None = None
    auto_allow: bool = True
    _state: dict[str, Any] = field(default_factory=lambda: {"__name__": "__main__"})
    _tool_names: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._executor = LocalPythonExecutor(["*"], timeout_seconds=self.timeout_seconds)

    @property
    def state(self) -> dict[str, Any]:
        return self._state

    def send_variables(self, variables: dict[str, Any]) -> None:
        self._state.update(variables)
        self._executor.send_variables(variables)

    def send_tools(self, tools: dict[str, Any]) -> None:
        self._tool_names = sorted(tools.keys())
        self._executor.send_tools(tools)

    def __call__(self, code_action: str) -> CodeOutput:
        if not self.auto_allow and self.permission_requester is not None:
            decision = str(
                self.permission_requester(
                    {
                        "kind": "python_exec_agent",
                        "toolCallId": f"{self.turn_id}:python_exec_agent",
                        "title": "Execute python code",
                    }
                )
            ).strip()
            if decision == "cancelled":
                raise RuntimeError("cancelled")
            if decision != "allow":
                raise RuntimeError("permission_denied")
        return self._executor(code_action)

    def release_run(self) -> None:
        return

    def json_safe_state(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in self._state.items():
            if key.startswith("_"):
                continue
            out[key] = self._json_safe_value(value)
        return out

    def _json_safe_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(k): self._json_safe_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._json_safe_value(item) for item in value]
        return str(value)
