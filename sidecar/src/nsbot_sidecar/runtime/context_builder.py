from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


BOOTSTRAP_FILES = ["SOUL.md", "USER.md", "TOOLS.md"]
IDENTITY_TEMPLATE_PATH = "templates/IDENTITFY.md"
SECTION_SEPARATOR = "\n\n---\n\n"

WINDOWS_POLICY_BLOCK = """## Platform Policy (Windows)
- You are running on Windows. Do not assume GNU tools like grep/sed/awk are available.
- Prefer Windows-native commands or file tools when they are more reliable.
- If terminal output is garbled, retry with UTF-8 output enabled.
"""

POSIX_POLICY_BLOCK = """## Platform Policy (POSIX)
- You are running on a POSIX system. Prefer UTF-8 and standard shell tools.
- Use file tools when they are simpler or more reliable than shell commands.
"""


class ContextBuildError(RuntimeError):
    """Raised when deterministic system prompt construction fails."""


@dataclass(frozen=True)
class ContextBuilderConfig:
    ns_bot_home: str
    workspace_path: str


@dataclass(frozen=True)
class RuntimeInfo:
    os_name: str
    arch: str
    python_version: str


class ContextBuilder:
    def build_system_prompt(self, config: ContextBuilderConfig, runtime_info: RuntimeInfo, memory_store) -> str:
        parts: list[str] = []

        identity = self._build_identity_layer(config.ns_bot_home, config.workspace_path, runtime_info)
        if identity:
            parts.append(identity)

        bootstrap = self._build_bootstrap_layer(config.ns_bot_home)
        if bootstrap:
            parts.append(bootstrap)

        memory = self._build_memory_layer(memory_store)
        if memory:
            parts.append(memory)

        return SECTION_SEPARATOR.join(parts)

    def _build_identity_layer(self, ns_bot_home: str, workspace_path: str, runtime_info: RuntimeInfo) -> str:
        template_path = Path(ns_bot_home) / IDENTITY_TEMPLATE_PATH
        if not template_path.exists():
            raise ContextBuildError(f"Identity template missing: {template_path}")

        try:
            template = template_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ContextBuildError(f"Identity template decode failed: {template_path}") from exc

        os_label = "macOS" if runtime_info.os_name == "Darwin" else runtime_info.os_name
        runtime_label = f"{os_label} {runtime_info.arch}, Python {runtime_info.python_version}"
        platform_policy = WINDOWS_POLICY_BLOCK if runtime_info.os_name == "Windows" else POSIX_POLICY_BLOCK

        rendered = template
        rendered = rendered.replace("{{runtime}}", runtime_label)
        rendered = rendered.replace("{{workspace_path}}", workspace_path)
        rendered = rendered.replace("{{platform_policy}}", platform_policy)
        unresolved = [
            marker
            for marker in ("{{runtime}}", "{{workspace_path}}", "{{platform_policy}}")
            if marker in rendered
        ]
        if unresolved:
            raise ContextBuildError(
                f"Identity template placeholder unresolved ({', '.join(unresolved)}): {template_path}"
            )

        return rendered

    def _build_bootstrap_layer(self, ns_bot_home: str) -> str:
        sections: list[str] = []
        root = Path(ns_bot_home) / "templates"

        for filename in BOOTSTRAP_FILES:
            target = root / filename
            if not target.exists():
                continue
            try:
                content = target.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ContextBuildError(f"Bootstrap template decode failed: {target}") from exc
            sections.append(f"## {filename}\n\n{content}")

        return "\n\n".join(sections)

    def _build_memory_layer(self, memory_store) -> str:
        long_term = memory_store.read_long_term()
        if long_term.strip() == "":
            return ""
        return "# Memory\n\n## Long-term Memory\n" + long_term
