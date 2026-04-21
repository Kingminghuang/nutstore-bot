#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIDECAR_ROOT = ROOT / "sidecar"
SRC_ROOT = SIDECAR_ROOT / "src"

for candidate in (str(SIDECAR_ROOT), str(SRC_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from smolagents.models import Model

from nsbot.runtime.context_builder import ContextBuilder, ContextBuilderConfig, RuntimeInfo
from nsbot.runtime.memory import MemoryStore
from nsbot.runtime.native_code_agent import NativeCodeAgent


class _NoopModel(Model):
    def __init__(self) -> None:
        super().__init__(model_id="noop")


def main() -> int:
    parser = argparse.ArgumentParser(description="Print NativeCodeAgent system prompt")
    parser.add_argument("--ns-bot-home", required=True)
    parser.add_argument("--workspace-path", required=True)
    parser.add_argument("--os-name", required=True)
    parser.add_argument("--arch", required=True)
    parser.add_argument("--python-version", required=True)
    args = parser.parse_args()

    builder = ContextBuilder()
    prompt_prefix = builder.build_system_prompt(
        ContextBuilderConfig(ns_bot_home=args.ns_bot_home, workspace_path=args.workspace_path),
        RuntimeInfo(os_name=args.os_name, arch=args.arch, python_version=args.python_version),
        MemoryStore(args.ns_bot_home),
    )

    agent = NativeCodeAgent(
        tools=[],
        model=_NoopModel(),
        context_prefix=prompt_prefix,
    )
    print(agent.system_prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
