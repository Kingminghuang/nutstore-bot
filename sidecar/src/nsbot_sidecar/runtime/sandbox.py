from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SessionModeId = Literal["read-only", "auto", "full-access"]
PermissionScenario = Literal["Exec", "Patch", "RequestPermissions", "McpElicitation"]
PermissionDecision = Literal["allow", "reject", "ask"]


@dataclass(frozen=True)
class SandboxPolicy:
    mode_id: SessionModeId
    approval_policy: Literal["on_request", "never"]
    sandbox_policy: Literal["read_only", "workspace_write", "full_access"]


@dataclass(frozen=True)
class SandboxPermissionResult:
    decision: PermissionDecision
    scenario: PermissionScenario


MODE_POLICIES: dict[SessionModeId, SandboxPolicy] = {
    "read-only": SandboxPolicy(
        mode_id="read-only",
        approval_policy="on_request",
        sandbox_policy="read_only",
    ),
    "auto": SandboxPolicy(
        mode_id="auto",
        approval_policy="on_request",
        sandbox_policy="workspace_write",
    ),
    "full-access": SandboxPolicy(
        mode_id="full-access",
        approval_policy="never",
        sandbox_policy="full_access",
    ),
}


class EmptySandbox:
    """Minimal sandbox policy surface.

    TODO:
    - add explicit network policy evaluation
    - add workspace-external path policy and amendment persistence
    - add executable prefix allow/deny amendment support
    """

    def __init__(self, *, workspace_path: str, mode_id: str):
        self.workspace_path = str(Path(workspace_path).expanduser().resolve())
        normalized_mode = str(mode_id or "").strip().lower() or "auto"
        if normalized_mode not in MODE_POLICIES:
            normalized_mode = "auto"
        self.policy = MODE_POLICIES[normalized_mode]  # type: ignore[index]

    @property
    def mode_id(self) -> SessionModeId:
        return self.policy.mode_id

    def evaluate_patch(self, path: str | None = None) -> SandboxPermissionResult:
        del path
        if self.policy.approval_policy == "never":
            return SandboxPermissionResult(decision="allow", scenario="Patch")
        if self.policy.sandbox_policy == "workspace_write":
            return SandboxPermissionResult(decision="allow", scenario="Patch")
        return SandboxPermissionResult(decision="ask", scenario="Patch")

    def evaluate_exec(self) -> SandboxPermissionResult:
        if self.policy.approval_policy == "never":
            return SandboxPermissionResult(decision="allow", scenario="Exec")
        return SandboxPermissionResult(decision="ask", scenario="Exec")

    def evaluate_dynamic_permission_request(self) -> SandboxPermissionResult:
        if self.policy.approval_policy == "never":
            return SandboxPermissionResult(decision="allow", scenario="RequestPermissions")
        return SandboxPermissionResult(decision="ask", scenario="RequestPermissions")

    def evaluate_mcp_tool_call(self) -> SandboxPermissionResult:
        if self.policy.approval_policy == "never":
            return SandboxPermissionResult(decision="allow", scenario="McpElicitation")
        return SandboxPermissionResult(decision="ask", scenario="McpElicitation")
