import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from local_paths import nsbot_home
from runtime_service import (
    CodeAgentRuntimeService,
    RunMetadata,
    RuntimeWorkerConfig,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sidecar CLI tool for invoking CodeAgentRuntimeService for integration testing"
    )
    parser.add_argument("user_input", type=str, help="The user input task to process")
    parser.add_argument(
        "--run-id",
        type=str,
        default=str(uuid.uuid4()),
        help="Run ID (defaults to a new UUID)",
    )
    parser.add_argument(
        "--workspace-path",
        type=str,
        default=os.getcwd(),
        help="Workspace directory path to use. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--model-id",
        type=str,
        default="gpt-5.4",
        help="Identifier for the primary model (e.g. gpt-5.4). Used as fallback if --direct-model-id is missing.",
    )
    parser.add_argument(
        "--direct-provider",
        type=str,
        default=os.getenv("DIRECT_PROVIDER", "custom"),
        choices=["anthropic", "deepseek", "gemini", "openai", "custom"],
        help="The provider to use. Valid choices are: anthropic, deepseek, gemini, openai, custom. Defaults to DIRECT_PROVIDER env var or 'custom'.",
    )
    parser.add_argument(
        "--direct-base-url",
        type=str,
        default=os.getenv("DIRECT_BASE_URL", ""),
        help="Optional base URL for the provider API (required for 'custom' or self-hosted OpenAI-compatible endpoints). Defaults to DIRECT_BASE_URL env var.",
    )
    parser.add_argument(
        "--direct-api-key",
        type=str,
        default=os.getenv("DIRECT_API_KEY", ""),
        help="API Key for the selected provider. Defaults to DIRECT_API_KEY env var.",
    )
    parser.add_argument(
        "--direct-model-id",
        type=str,
        default=os.getenv("DIRECT_MODEL_ID", ""),
        help="The specific model ID to use with the direct provider (e.g., deepseek-reasoner). Defaults to DIRECT_MODEL_ID env var.",
    )
    parser.add_argument(
        "--direct-request-timeout-ms",
        type=int,
        default=60000,
        help="Request timeout in milliseconds for API calls. Defaults to 60000 (60s).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="Maximum number of turns/steps the agent can execute. Defaults to 20.",
    )
    parser.add_argument(
        "--ns-bot-home",
        type=str,
        default=str(nsbot_home()),
        help="Path to the bot's home directory for session and memory storage. Defaults to the platform-specific NutstoreBot data directory.",
    )
    parser.add_argument(
        "--fd-executable",
        type=str,
        default="",
        help="Path to the 'fd' executable tool. Defaults to empty (auto-discovery).",
    )
    parser.add_argument(
        "--rg-executable",
        type=str,
        default="",
        help="Path to the 'rg' (ripgrep) executable tool. Defaults to empty (auto-discovery).",
    )
    parser.add_argument(
        "--tool-os-type",
        type=str,
        default="",
        help="Target OS type for tooling (e.g., win32, darwin). Defaults to current OS.",
    )
    parser.add_argument(
        "--session-key",
        type=str,
        default="",
        help="Custom session key for continuing a previous conversation context. Defaults to a workspace-derived key.",
    )
    parser.add_argument(
        "--dump-result",
        action="store_true",
        help="Dump the entire JSON result at the end of the run",
    )

    args = parser.parse_args()

    config = RuntimeWorkerConfig(
        model_id=args.model_id,
        direct_provider=args.direct_provider or None,
        direct_base_url=args.direct_base_url or None,
        direct_api_key=args.direct_api_key or None,
        direct_model_id=args.direct_model_id or args.model_id,
        direct_request_timeout_ms=args.direct_request_timeout_ms,
        ns_bot_home=args.ns_bot_home,
        workspace_path_default=args.workspace_path,
        fd_executable=args.fd_executable or None,
        rg_executable=args.rg_executable or None,
        tool_os_type=args.tool_os_type or None,
        max_steps=args.max_steps,
    )

    metadata = RunMetadata(
        workspace_path=args.workspace_path,
        session_key=args.session_key or None,
    )

    auth_context = {
        "uid": "cli-user",
        "tid": "cli-team",
        "exp_epoch": 0,
    }

    print(f"[*] Initializing CodeAgentRuntimeService", file=sys.stderr)
    print(f"[*] Workspace: {args.workspace_path}", file=sys.stderr)
    model_disp = config.direct_model_id or config.model_id
    print(
        f"[*] Model: {model_disp} (Provider: {config.direct_provider})", file=sys.stderr
    )
    print(f"[*] Base URL: {config.direct_base_url}", file=sys.stderr)

    try:
        service = CodeAgentRuntimeService(config)
        print(f"\n[*] Processing user input: {args.user_input}", file=sys.stderr)
        print("-" * 50, file=sys.stderr)

        # NOTE: NativeCodeAgent stream_outputs=True will print natively
        # as it progresses if stdout isn't captured.

        result = service.process(
            run_id=args.run_id,
            user_input=args.user_input,
            auth_context=auth_context,
            metadata=metadata,
        )

        print("\n" + "=" * 50, file=sys.stderr)
        print("FINAL ANSWER:", file=sys.stderr)
        if result and "final_answer" in result:
            print(result["final_answer"])
        else:
            print("No final answer returned.", file=sys.stderr)

        if args.dump_result:
            print("\nRAW RESULT:", file=sys.stderr)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"\n[!] Error during execution: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
