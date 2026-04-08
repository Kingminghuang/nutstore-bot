import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse
import uuid
import webbrowser
from typing import Any, cast

from fastapi import HTTPException
import requests

from attachment_store import AttachmentStore
from local_paths import nsbot_home
from provider_service import ProviderService
from repositories import ProviderConnectionBundle, create_repositories
from runtime_service import CodeAgentRuntimeService, RunMetadata, RuntimeWorkerConfig
from session_service import SessionService
from secret_store import LocalSecretStore
from storage import connect_database
from timeline_service import TimelineService
from workspace_sidecar_indexer import WorkspaceSidecarIndexer


COMMANDS = {
    "run",
    "providers",
    "models",
    "auth",
    "init",
    "workspaces",
    "sessions",
    "help",
}
NUTSTORE_CUSTOM_SLUG = "nutstore"
NUTSTORE_DISPLAY_NAME = "Nut Store"
AUTH_PENDING_FILE = "auth-nutstore-pending.json"
AUTH_PENDING_TTL_SECONDS = 15 * 60
AUTH_REQUEST_TIMEOUT_SECONDS = 30


def _normalize_provider_ref(bundle: dict[str, Any]) -> str:
    return str(
        bundle.get("catalogProviderId")
        or bundle.get("customSlug")
        or bundle.get("runtimeProvider")
        or ""
    )


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _normalize_base_url(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if normalized == "":
        raise ValueError("gateway-base-url is required")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or parsed.netloc == "":
        raise ValueError("gateway-base-url must be an absolute http(s) URL")
    return normalized


def _pending_auth_file(ns_bot_home_value: str) -> Path:
    return Path(ns_bot_home_value).expanduser().resolve() / AUTH_PENDING_FILE


def _save_pending_auth(ns_bot_home_value: str, payload: dict[str, Any]) -> None:
    path = _pending_auth_file(ns_bot_home_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _load_pending_auth(ns_bot_home_value: str) -> dict[str, Any]:
    path = _pending_auth_file(ns_bot_home_value)
    if not path.exists():
        raise ValueError("No pending auth login found. Run `auth login` first.")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    created_epoch = float(payload.get("createdEpoch") or 0)
    if created_epoch <= 0:
        _clear_pending_auth(ns_bot_home_value)
        raise ValueError(
            "Pending auth login metadata is invalid. Run `auth login` again."
        )
    if (time.time() - created_epoch) > AUTH_PENDING_TTL_SECONDS:
        _clear_pending_auth(ns_bot_home_value)
        raise ValueError("Pending auth login expired. Run `auth login` again.")
    return payload


def _clear_pending_auth(ns_bot_home_value: str) -> None:
    path = _pending_auth_file(ns_bot_home_value)
    if path.exists():
        path.unlink()


def _build_authorize_url(
    *, gateway_base_url: str, redirect_uri: str, state: str, nonce: str
) -> str:
    query = urlencode(
        {
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
        }
    )
    return f"{gateway_base_url}/d/openid/auth?{query}"


def _extract_code_and_state(raw_input: str) -> tuple[str, str | None]:
    value = str(raw_input or "").strip()
    if value == "":
        raise ValueError("OAuth input is required")
    if "://" not in value:
        return value, None
    parsed = urlparse(value)
    params = parse_qs(parsed.query)
    code = str((params.get("code") or [""])[0]).strip()
    state = str((params.get("state") or [""])[0]).strip() or None
    if code == "":
        raise ValueError("OAuth callback URL is missing code")
    return code, state


def _wait_for_local_callback(
    *, redirect_uri: str, timeout_seconds: int
) -> tuple[str, str | None] | None:
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or ""
    if parsed.scheme != "http" or host not in {"localhost", "127.0.0.1"}:
        return None
    if parsed.port is None:
        raise ValueError(
            "redirect URI must include an explicit port for local callback"
        )

    expected_path = parsed.path or "/"
    callback_payload: dict[str, str | None] = {"code": None, "state": None}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            request_url = urlparse(self.path)
            if request_url.path != expected_path:
                self.send_response(404)
                self.end_headers()
                return

            params = parse_qs(request_url.query)
            code = str((params.get("code") or [""])[0]).strip()
            state = str((params.get("state") or [""])[0]).strip() or None
            if code == "":
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code")
                return

            callback_payload["code"] = code
            callback_payload["state"] = state
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Nut Store login complete</h2><p>You can close this tab.</p></body></html>"
            )

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    bind_host = "127.0.0.1"
    try:
        server = HTTPServer((bind_host, parsed.port), CallbackHandler)
    except OSError as exc:
        raise ValueError(
            f"Failed to bind callback listener at {bind_host}:{parsed.port}: {exc}"
        ) from exc

    deadline = time.monotonic() + max(timeout_seconds, 1)
    try:
        while callback_payload.get("code") is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            server.timeout = min(1.0, remaining)
            server.handle_request()
    finally:
        server.server_close()

    code = callback_payload.get("code")
    if code is None:
        return None
    return code, callback_payload.get("state")


def _exchange_oidc_code(
    *,
    gateway_base_url: str,
    code: str,
    redirect_uri: str,
    nonce: str,
) -> dict[str, Any]:
    response = requests.post(
        f"{gateway_base_url}/v1/auth/oidc/exchange",
        json={
            "code": code,
            "redirect_uri": redirect_uri,
            "nonce": nonce,
        },
        timeout=AUTH_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    access_token = str(payload.get("access_token") or "").strip()
    if access_token == "":
        raise ValueError("Gateway exchange response missing access_token")
    return payload


def _find_nutstore_bundle(
    bundles: list[ProviderConnectionBundle],
) -> ProviderConnectionBundle | None:
    for bundle in bundles:
        if bundle.connection.kind != "custom":
            continue
        if (bundle.connection.custom_slug or "") != NUTSTORE_CUSTOM_SLUG:
            continue
        return bundle
    return None


def _upsert_nutstore_provider(
    *,
    repositories: Any,
    provider_service: ProviderService,
    gateway_base_url: str,
    access_token: str,
    model_id: str,
) -> dict[str, Any]:
    existing = _find_nutstore_bundle(repositories.providers.list_bundles())
    base_url = f"{gateway_base_url}/v1"

    custom_models: list[dict[str, Any]] = []
    if existing is not None:
        custom_models = [
            {
                "id": model.id,
                "modelId": model.model_id,
                "displayName": model.display_name,
                "enabled": model.enabled,
            }
            for model in existing.models
            if model.source == "custom"
        ]

    model_ids = {str(model.get("modelId") or "") for model in custom_models}
    if model_id not in model_ids:
        custom_models.append(
            {
                "modelId": model_id,
                "displayName": model_id,
                "enabled": True,
            }
        )

    payload = {
        "kind": "custom",
        "customSlug": NUTSTORE_CUSTOM_SLUG,
        "displayName": NUTSTORE_DISPLAY_NAME,
        "baseUrl": base_url,
        "apiKey": access_token,
        "preferredModelId": model_id,
        "customModels": custom_models,
        "healthStatus": "connected",
        "healthMessage": "Authenticated via OIDC",
    }

    if existing is None:
        return provider_service.create_provider(payload)
    return provider_service.update_provider(existing.connection.id, payload)


def _runtime_paths() -> dict[str, Path]:
    sidecar_root = Path(__file__).resolve().parent
    repo_root = sidecar_root.parent
    return {
        "repo_root": repo_root,
        "sidecar_root": sidecar_root,
        "templates_dir": repo_root / "templates",
        "search_tools_cache_root": sidecar_root / "vendor" / "search-tools",
        "prepare_search_tools_script": sidecar_root
        / "scripts"
        / "prepare_search_tools.py",
    }


def _resolve_target_triple() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        if machine in {"arm64", "aarch64"}:
            return "aarch64-apple-darwin"
        if machine in {"x86_64", "amd64"}:
            return "x86_64-apple-darwin"
    if sys.platform.startswith("win") and machine in {"x86_64", "amd64"}:
        return "x86_64-pc-windows-msvc"
    raise ValueError(
        f"Unsupported platform/arch for vendored search tools: {sys.platform}/{machine}"
    )


def _resolve_binary_name(tool_name: str) -> str:
    return f"{tool_name}.exe" if sys.platform.startswith("win") else tool_name


def _run_prepare_search_tools(
    *,
    sidecar_root: Path,
    target: str,
    prepare_search_tools_script: Path,
) -> None:
    if not prepare_search_tools_script.exists():
        raise RuntimeError(f"Missing prepare script: {prepare_search_tools_script}")
    result = subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(prepare_search_tools_script.relative_to(sidecar_root)),
            "--target",
            target,
        ],
        cwd=str(sidecar_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    detail = stderr or stdout or "Failed to prepare search tools"
    raise RuntimeError(detail)


def _handle_init_command(args: argparse.Namespace) -> int:
    ns_bot_home = Path(args.ns_bot_home).expanduser().resolve()
    ns_bot_home_existed = ns_bot_home.exists()
    ns_bot_home.mkdir(parents=True, exist_ok=True)
    bin_dir = ns_bot_home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    runtime_paths = _runtime_paths()
    templates_source = runtime_paths["templates_dir"]
    templates_target = ns_bot_home / "templates"
    templates_copied = False
    if templates_source.exists() and not templates_target.exists():
        shutil.copytree(templates_source, templates_target)
        templates_copied = True

    target = _resolve_target_triple()
    fd_binary_name = _resolve_binary_name("fd")
    rg_binary_name = _resolve_binary_name("rg")
    fd_target = bin_dir / fd_binary_name
    rg_target = bin_dir / rg_binary_name
    fd_cache = runtime_paths["search_tools_cache_root"] / target / "fd" / fd_binary_name
    rg_cache = runtime_paths["search_tools_cache_root"] / target / "rg" / rg_binary_name

    search_tools_downloaded = False
    search_tools_copied = False
    need_fd = not fd_target.exists()
    need_rg = not rg_target.exists()
    if need_fd or need_rg:
        if not fd_cache.exists() or not rg_cache.exists():
            _run_prepare_search_tools(
                sidecar_root=runtime_paths["sidecar_root"],
                target=target,
                prepare_search_tools_script=runtime_paths[
                    "prepare_search_tools_script"
                ],
            )
            search_tools_downloaded = True

        if need_fd:
            if not fd_cache.exists():
                raise RuntimeError(f"Missing vendored fd binary: {fd_cache}")
            shutil.copy2(fd_cache, fd_target)
            search_tools_copied = True
        if need_rg:
            if not rg_cache.exists():
                raise RuntimeError(f"Missing vendored rg binary: {rg_cache}")
            shutil.copy2(rg_cache, rg_target)
            search_tools_copied = True

    if not sys.platform.startswith("win"):
        os.chmod(fd_target, 0o755)
        os.chmod(rg_target, 0o755)

    _print_json(
        {
            "ok": True,
            "nsBotHome": str(ns_bot_home),
            "templatesPath": str(templates_target),
            "fdExecutable": str(fd_target),
            "rgExecutable": str(rg_target),
            "prepared": {
                "nsBotHomeCreated": not ns_bot_home_existed,
                "templatesCopied": templates_copied,
                "searchToolsCopied": search_tools_copied,
                "searchToolsDownloaded": search_tools_downloaded,
            },
        }
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NSBot CLI for provider/model management and runtime execution"
    )
    parser.add_argument(
        "--ns-bot-home",
        type=str,
        default=str(nsbot_home()),
        help="Path to NSBot data directory.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    providers = subparsers.add_parser("providers", help="Manage provider connections")
    providers_sub = providers.add_subparsers(dest="providers_command", required=True)
    providers_sub.add_parser("list", help="List catalog providers and connections")

    providers_use = providers_sub.add_parser(
        "use", help="Set preferred model for provider/connection"
    )
    providers_use.add_argument(
        "provider", type=str, help="Provider id or connection id"
    )
    providers_use.add_argument(
        "--model",
        type=str,
        default="",
        help="Preferred model id. If omitted, use first available model.",
    )

    providers_delete = providers_sub.add_parser(
        "delete", help="Delete a provider connection"
    )
    providers_delete.add_argument(
        "--connection-id", type=str, required=True, help="Provider connection id"
    )

    models = subparsers.add_parser("models", help="Manage model options")
    models_sub = models.add_subparsers(dest="models_command", required=True)

    models_list = models_sub.add_parser("list", help="List model options")
    models_list.add_argument(
        "--provider", type=str, default="", help="Filter by provider id"
    )
    models_list.add_argument(
        "--connection-id", type=str, default="", help="Filter by connection id"
    )
    models_sub.add_parser("status", help="Show current default selection")

    models_enable = models_sub.add_parser("enable", help="Enable a model")
    models_enable.add_argument("--connection-id", type=str, required=True)
    models_enable.add_argument("--model", type=str, required=True)

    models_disable = models_sub.add_parser("disable", help="Disable a model")
    models_disable.add_argument("--connection-id", type=str, required=True)
    models_disable.add_argument("--model", type=str, required=True)

    models_remove = models_sub.add_parser(
        "remove", help="Remove a custom model from a custom provider"
    )
    models_remove.add_argument("--connection-id", type=str, required=True)
    models_remove.add_argument("--model", type=str, required=True)

    auth = subparsers.add_parser("auth", help="Authenticate Nut Store gateway")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)

    auth_login = auth_sub.add_parser("login", help="Start Nut Store OAuth login")
    auth_login.add_argument(
        "--gateway-base-url",
        type=str,
        default=os.getenv("NS_GATEWAY_BASE_URL", "").strip(),
        help="Gateway base URL used for authorize/exchange endpoints",
    )
    auth_login.add_argument(
        "--model",
        type=str,
        default="gpt-5.4",
        help="Preferred model id for Nut Store connection",
    )
    auth_login.add_argument(
        "--redirect-uri",
        type=str,
        default="",
        help="OAuth redirect URI. Defaults to local callback URL",
    )
    auth_login.add_argument(
        "--callback-port",
        type=int,
        default=1457,
        help="Port used for local callback listener",
    )
    auth_login.add_argument(
        "--callback-timeout-sec",
        type=int,
        default=120,
        help="Seconds to wait for browser callback before timeout",
    )
    auth_login.add_argument(
        "--no-open",
        action="store_true",
        help="Do not auto-open browser, print authorize URL only",
    )
    auth_login.add_argument(
        "--no-listen",
        action="store_true",
        help="Skip local callback listener and complete via paste-redirect",
    )

    auth_paste = auth_sub.add_parser(
        "paste-redirect", help="Complete OAuth with pasted callback URL or code"
    )
    auth_paste.add_argument(
        "--input",
        type=str,
        required=True,
        help="Full callback URL or raw OAuth code",
    )
    auth_paste.add_argument(
        "--gateway-base-url",
        type=str,
        default="",
        help="Optional override for gateway base URL",
    )
    auth_paste.add_argument(
        "--model",
        type=str,
        default="",
        help="Optional override for preferred model id",
    )

    subparsers.add_parser("init", help="Initialize NS_BOT_HOME resources")

    workspaces = subparsers.add_parser("workspaces", help="Manage workspaces")
    workspaces_sub = workspaces.add_subparsers(dest="workspaces_command", required=True)
    workspaces_sub.add_parser("list", help="List workspaces")

    workspaces_create = workspaces_sub.add_parser("create", help="Create a workspace")
    workspaces_create.add_argument("--name", type=str, required=True)
    workspaces_create.add_argument("--real-path", type=str, required=True)
    workspaces_create.add_argument(
        "--path-label",
        type=str,
        default="",
        help="Path label shown to users (default: --real-path)",
    )

    workspaces_update = workspaces_sub.add_parser("update", help="Update a workspace")
    workspaces_update.add_argument("--workspace-id", type=str, required=True)
    workspaces_update.add_argument("--name", type=str, default="")
    workspaces_update.add_argument("--real-path", type=str, default="")
    workspaces_update.add_argument("--path-label", type=str, default="")

    workspaces_delete = workspaces_sub.add_parser("delete", help="Delete a workspace")
    workspaces_delete.add_argument("--workspace-id", type=str, required=True)

    workspaces_index_status = workspaces_sub.add_parser(
        "sidecar-index-status", help="Show workspace sidecar index status"
    )
    workspaces_index_status.add_argument("--workspace-id", type=str, required=True)

    sessions = subparsers.add_parser("sessions", help="Manage sessions")
    sessions_sub = sessions.add_subparsers(dest="sessions_command", required=True)

    sessions_list = sessions_sub.add_parser("list", help="List sessions in workspace")
    sessions_list.add_argument("--workspace-id", type=str, required=True)

    sessions_create = sessions_sub.add_parser("create", help="Create a session")
    sessions_create.add_argument("--workspace-id", type=str, required=True)
    sessions_create.add_argument("--connection-id", type=str, default="")
    sessions_create.add_argument("--model-id", type=str, default="")

    sessions_update = sessions_sub.add_parser("update", help="Update a session")
    sessions_update.add_argument("--session-id", type=str, required=True)
    sessions_update.add_argument("--title", type=str, required=True)

    sessions_delete = sessions_sub.add_parser("delete", help="Delete a session")
    sessions_delete.add_argument("--session-id", type=str, required=True)

    sessions_timeline = sessions_sub.add_parser(
        "timeline", help="Show session timeline"
    )
    sessions_timeline.add_argument("--session-id", type=str, required=True)
    sessions_timeline.add_argument("--limit", type=int, default=None)
    sessions_timeline.add_argument("--before-sequence", type=int, default=None)

    run = subparsers.add_parser("run", help="Execute one task")
    run.add_argument("user_input", type=str, help="Task prompt")
    run.add_argument(
        "--run-id",
        type=str,
        default=str(uuid.uuid4()),
        help="Run ID (defaults to a new UUID)",
    )
    run.add_argument(
        "--workspace-path",
        type=str,
        default=os.getcwd(),
        help="Workspace directory path (default: current directory)",
    )
    run.add_argument(
        "--model-id",
        type=str,
        default="gpt-5.4",
        help="Primary model id for memory/title fallback",
    )
    run.add_argument(
        "--connection-id",
        type=str,
        default="",
        help="Use this provider connection id from database",
    )
    run.add_argument(
        "--selected-model-id",
        type=str,
        default="",
        help="Model id to pair with --connection-id",
    )
    run.add_argument(
        "--provider",
        type=str,
        default=os.getenv("PROVIDER", "").strip(),
        help="Runtime provider override",
    )
    run.add_argument(
        "--base-url",
        type=str,
        default=os.getenv("BASE_URL", "").strip(),
        help="Runtime base URL override",
    )
    run.add_argument(
        "--api-key",
        type=str,
        default=os.getenv("API_KEY", "").strip(),
        help="Runtime API key override",
    )
    run.add_argument(
        "--model",
        type=str,
        default=os.getenv("MODEL", "").strip(),
        help="Runtime model id override",
    )
    run.add_argument(
        "--request-timeout-ms",
        type=int,
        default=60000,
        help="Request timeout in milliseconds",
    )
    run.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="Maximum number of turns/steps",
    )
    run.add_argument(
        "--fd-executable",
        type=str,
        default=os.getenv("NSBOT_FD_EXECUTABLE", "").strip(),
        help="Path to fd executable",
    )
    run.add_argument(
        "--rg-executable",
        type=str,
        default=os.getenv("NSBOT_RG_EXECUTABLE", "").strip(),
        help="Path to rg executable",
    )
    run.add_argument(
        "--tool-os-type",
        type=str,
        default="",
        help="Target OS type for tooling",
    )
    run.add_argument(
        "--session-key",
        type=str,
        default="",
        help="Session key override",
    )
    run.add_argument(
        "--session-id",
        type=str,
        default="",
        help="Session id in DB (overrides workspace/session-key when set)",
    )
    run.add_argument(
        "--dump-result",
        action="store_true",
        help="Dump whole JSON result",
    )
    run.add_argument(
        "--diagnose",
        action="store_true",
        help="Resolve provider/model and print config only",
    )

    help_cmd = subparsers.add_parser("help", help="Show help for command/subcommand")
    help_cmd.add_argument(
        "topic",
        nargs="*",
        help="Command path, e.g. run or providers use",
    )
    return parser


def _ensure_run_compat(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if argv[0] == "help":
        return argv
    if any(token in {"-h", "--help"} for token in argv):
        return argv
    if any(token in COMMANDS for token in argv):
        return argv
    return ["run", *argv]


def _print_help_for_topic(parser: argparse.ArgumentParser, topic: list[str]) -> int:
    current = parser
    for token in topic:
        subparsers_action = None
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                subparsers_action = action
                break
        if subparsers_action is None:
            print(f"Unknown help topic: {' '.join(topic)}", file=sys.stderr)
            return 1
        next_parser = subparsers_action.choices.get(token)
        if next_parser is None:
            print(f"Unknown help topic: {' '.join(topic)}", file=sys.stderr)
            return 1
        current = next_parser
    current.print_help()
    return 0


def _build_services(ns_bot_home_value: str):
    database = connect_database(ns_bot_home_value)
    repositories = create_repositories(cast(Any, database))
    secret_store = LocalSecretStore(ns_bot_home_value)
    provider_service = ProviderService(
        repositories=repositories.providers,
        secret_store=secret_store,
    )
    return database, repositories, secret_store, provider_service


def _build_session_service(ns_bot_home_value: str):
    database = connect_database(ns_bot_home_value)
    repositories = create_repositories(cast(Any, database))
    session_service = SessionService(
        workspaces=repositories.workspaces,
        sessions=repositories.sessions,
        attachments=repositories.attachments,
        draft_attachments=repositories.draft_attachments,
        attachment_store=AttachmentStore(ns_bot_home_value),
        timeline_service=TimelineService(
            sessions=repositories.sessions,
            timeline_entries=repositories.timeline_entries,
        ),
        workspace_sidecar_indexer=WorkspaceSidecarIndexer(),
    )
    return database, repositories, session_service


def _http_detail(exc: HTTPException) -> str:
    if isinstance(exc.detail, dict):
        return json.dumps(exc.detail, ensure_ascii=False)
    return str(exc.detail)


def _find_target_group(
    model_options: dict[str, Any], *, provider_ref: str
) -> dict[str, Any] | None:
    groups = model_options.get("groups")
    if not isinstance(groups, list):
        return None
    for group in groups:
        connection_id = str(group.get("connectionId") or "")
        provider_id = str(group.get("providerId") or "")
        if provider_ref == connection_id or provider_ref == provider_id:
            return group
    return None


def _require_connection(
    bundles: list[dict[str, Any]], connection_id: str
) -> dict[str, Any] | None:
    for connection in bundles:
        if str(connection.get("id") or "") == connection_id:
            return connection
    return None


def _first_model_id(group: dict[str, Any]) -> str | None:
    models = group.get("models")
    if not isinstance(models, list) or not models:
        return None
    model_id = str(models[0].get("modelId") or "").strip()
    return model_id or None


def _resolve_catalog_model_ids(bundle: ProviderConnectionBundle) -> list[str]:
    from provider_catalog import list_providers

    provider_id = bundle.connection.catalog_provider_id or ""
    for provider in list_providers():
        if str(provider.get("id") or "") != provider_id:
            continue
        models = provider.get("models")
        if not isinstance(models, list):
            return []
        return [str(model.get("id") or "") for model in models if model.get("id")]
    return []


def _handle_providers_command(args: argparse.Namespace) -> int:
    database, repositories, _secret_store, provider_service = _build_services(
        args.ns_bot_home
    )
    try:
        if args.providers_command == "list":
            catalog = provider_service.catalog_payload()
            connections = provider_service.list_connections_payload()
            _print_json(
                {
                    "providers": catalog.get("providers", []),
                    "connections": connections.get("connections", []),
                }
            )
            return 0

        if args.providers_command == "delete":
            provider_service.delete_provider(args.connection_id)
            _print_json({"ok": True, "deletedConnectionId": args.connection_id})
            return 0

        if args.providers_command == "use":
            target_ref = str(args.provider or "").strip()
            if target_ref == "":
                raise ValueError("Provider id or connection id is required")

            model_options = provider_service.model_options_payload()
            group = _find_target_group(model_options, provider_ref=target_ref)
            if group is None:
                raise ValueError(
                    f"No available connected provider found for '{target_ref}'"
                )

            connection_id = str(group.get("connectionId") or "")
            requested_model = str(args.model or "").strip()
            model_id = requested_model or _first_model_id(group)
            if model_id is None:
                raise ValueError(f"No available models for '{target_ref}'")

            models = group.get("models")
            allowed_ids = {
                str(model.get("modelId") or "")
                for model in (models if isinstance(models, list) else [])
            }
            if model_id not in allowed_ids:
                raise ValueError(
                    f"Model '{model_id}' is not available for connection '{connection_id}'"
                )

            updated = provider_service.update_provider(
                connection_id, {"preferredModelId": model_id}
            )
            _print_json(
                {
                    "ok": True,
                    "connectionId": connection_id,
                    "providerId": _normalize_provider_ref(updated),
                    "modelId": model_id,
                }
            )
            return 0

        raise ValueError(f"Unknown providers command: {args.providers_command}")
    finally:
        database.close()


def _handle_models_command(args: argparse.Namespace) -> int:
    database, repositories, _secret_store, provider_service = _build_services(
        args.ns_bot_home
    )
    try:
        if args.models_command == "list":
            payload = provider_service.model_options_payload()
            groups = payload.get("groups")
            filtered = []
            provider_filter = str(args.provider or "").strip()
            connection_filter = str(args.connection_id or "").strip()
            for group in groups if isinstance(groups, list) else []:
                provider_id = str(group.get("providerId") or "")
                connection_id = str(group.get("connectionId") or "")
                if provider_filter and provider_filter != provider_id:
                    continue
                if connection_filter and connection_filter != connection_id:
                    continue
                filtered.append(group)
            _print_json({"groups": filtered})
            return 0

        if args.models_command == "status":
            payload = provider_service.model_options_payload()
            default_selection = payload.get("defaultSelection")
            groups = payload.get("groups")
            default_group = None
            if isinstance(default_selection, dict):
                selected_connection = str(default_selection.get("connectionId") or "")
                for group in groups if isinstance(groups, list) else []:
                    if str(group.get("connectionId") or "") == selected_connection:
                        default_group = group
                        break
            _print_json(
                {
                    "defaultSelection": default_selection,
                    "defaultGroup": default_group,
                    "groupCount": len(groups) if isinstance(groups, list) else 0,
                }
            )
            return 0

        connection_id = str(args.connection_id or "").strip()
        model_id = str(args.model or "").strip()
        bundle = repositories.providers.get_bundle_by_id(connection_id)
        if bundle is None:
            raise ValueError(f"Provider connection not found: {connection_id}")

        if args.models_command == "remove":
            if bundle.connection.kind != "custom":
                raise ValueError(
                    "models remove is only supported for custom provider connections"
                )
            custom_models = [
                {
                    "id": model.id,
                    "modelId": model.model_id,
                    "displayName": model.display_name,
                    "enabled": model.enabled,
                }
                for model in bundle.models
                if model.source == "custom" and model.model_id != model_id
            ]
            if len(custom_models) == len(
                [model for model in bundle.models if model.source == "custom"]
            ):
                raise ValueError(
                    f"Model '{model_id}' not found in connection '{connection_id}'"
                )

            next_preferred = bundle.connection.preferred_model_id
            if next_preferred == model_id:
                replacement = next(
                    (
                        str(model.get("modelId") or "")
                        for model in custom_models
                        if bool(model.get("enabled", True))
                    ),
                    None,
                )
                next_preferred = replacement

            provider_service.update_provider(
                connection_id,
                {
                    "customModels": custom_models,
                    "preferredModelId": next_preferred,
                },
            )
            _print_json(
                {
                    "ok": True,
                    "connectionId": connection_id,
                    "modelId": model_id,
                    "action": "removed",
                }
            )
            return 0

        if bundle.connection.kind == "custom":
            custom_models = []
            found = False
            for model in bundle.models:
                if model.source != "custom":
                    continue
                enabled = model.enabled
                if model.model_id == model_id:
                    found = True
                    enabled = args.models_command == "enable"
                custom_models.append(
                    {
                        "id": model.id,
                        "modelId": model.model_id,
                        "displayName": model.display_name,
                        "enabled": enabled,
                    }
                )
            if not found:
                raise ValueError(
                    f"Model '{model_id}' not found in connection '{connection_id}'"
                )

            provider_service.update_provider(
                connection_id,
                {
                    "customModels": custom_models,
                    "preferredModelId": bundle.connection.preferred_model_id,
                },
            )
            _print_json(
                {
                    "ok": True,
                    "connectionId": connection_id,
                    "modelId": model_id,
                    "action": "enabled"
                    if args.models_command == "enable"
                    else "disabled",
                }
            )
            return 0

        catalog_ids = _resolve_catalog_model_ids(bundle)
        if model_id not in set(catalog_ids):
            raise ValueError(
                f"Model '{model_id}' is not a catalog model for connection '{connection_id}'"
            )

        if (
            args.models_command == "enable"
            and bundle.connection.model_policy != "restricted"
        ):
            _print_json(
                {
                    "ok": True,
                    "connectionId": connection_id,
                    "modelId": model_id,
                    "action": "enabled",
                    "modelPolicy": bundle.connection.model_policy,
                }
            )
            return 0

        if bundle.connection.model_policy == "restricted":
            enabled_set = {
                model.model_id
                for model in bundle.models
                if model.source == "catalog" and model.enabled
            }
        else:
            enabled_set = set(catalog_ids)

        if args.models_command == "enable":
            enabled_set.add(model_id)
        elif args.models_command == "disable":
            enabled_set.discard(model_id)

        enabled_in_order = [item for item in catalog_ids if item in enabled_set]
        provider_service.update_provider(
            connection_id,
            {
                "modelPolicy": "restricted",
                "enabledModelIds": enabled_in_order,
                "preferredModelId": bundle.connection.preferred_model_id,
            },
        )
        _print_json(
            {
                "ok": True,
                "connectionId": connection_id,
                "modelId": model_id,
                "action": "enabled" if args.models_command == "enable" else "disabled",
            }
        )
        return 0
    finally:
        database.close()


def _handle_workspaces_command(args: argparse.Namespace) -> int:
    database, _repositories, session_service = _build_session_service(args.ns_bot_home)
    try:
        if args.workspaces_command == "list":
            _print_json(session_service.list_workspaces_payload())
            return 0

        if args.workspaces_command == "create":
            payload = {
                "name": str(args.name or "").strip(),
                "realPath": str(args.real_path or "").strip(),
                "pathLabel": str(args.path_label or "").strip()
                or str(args.real_path or "").strip(),
            }
            try:
                created = session_service.create_workspace(payload)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(created)
            return 0

        if args.workspaces_command == "update":
            payload: dict[str, Any] = {}
            name = str(args.name or "").strip()
            real_path = str(args.real_path or "").strip()
            path_label = str(args.path_label or "").strip()
            if name:
                payload["name"] = name
            if real_path:
                payload["realPath"] = real_path
            if path_label:
                payload["pathLabel"] = path_label
            if not payload:
                raise ValueError(
                    "At least one field is required: --name/--real-path/--path-label"
                )
            try:
                updated = session_service.update_workspace(args.workspace_id, payload)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(updated)
            return 0

        if args.workspaces_command == "delete":
            try:
                session_service.delete_workspace(args.workspace_id)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(
                {"ok": True, "workspaceId": args.workspace_id, "action": "deleted"}
            )
            return 0

        if args.workspaces_command == "sidecar-index-status":
            try:
                payload = session_service.workspace_sidecar_index_status_payload(
                    args.workspace_id
                )
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(payload)
            return 0

        raise ValueError(f"Unknown workspaces command: {args.workspaces_command}")
    finally:
        database.close()


def _handle_sessions_command(args: argparse.Namespace) -> int:
    database, _repositories, session_service = _build_session_service(args.ns_bot_home)
    try:
        if args.sessions_command == "list":
            try:
                payload = session_service.list_sessions_payload(args.workspace_id)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(payload)
            return 0

        if args.sessions_command == "create":
            payload: dict[str, Any] = {}
            connection_id = str(args.connection_id or "").strip()
            model_id = str(args.model_id or "").strip()
            if connection_id:
                payload["connectionId"] = connection_id
            if model_id:
                payload["modelId"] = model_id
            try:
                created = session_service.create_session(args.workspace_id, payload)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(created)
            return 0

        if args.sessions_command == "update":
            payload = {"title": str(args.title or "").strip()}
            try:
                updated = session_service.update_session(args.session_id, payload)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(updated)
            return 0

        if args.sessions_command == "delete":
            try:
                session_service.delete_session(args.session_id)
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json({"ok": True, "sessionId": args.session_id, "action": "deleted"})
            return 0

        if args.sessions_command == "timeline":
            try:
                payload = session_service.list_timeline_payload(
                    args.session_id,
                    limit=args.limit,
                    before_sequence=args.before_sequence,
                )
            except HTTPException as exc:
                raise ValueError(_http_detail(exc)) from exc
            _print_json(payload)
            return 0

        raise ValueError(f"Unknown sessions command: {args.sessions_command}")
    finally:
        database.close()


def _handle_auth_login_command(args: argparse.Namespace) -> int:
    gateway_base_url = _normalize_base_url(args.gateway_base_url)
    model_id = str(args.model or "").strip() or "gpt-5.4"
    redirect_uri = str(args.redirect_uri or "").strip() or (
        f"http://localhost:{int(args.callback_port)}/auth/callback"
    )
    timeout_seconds = int(args.callback_timeout_sec)
    if timeout_seconds <= 0:
        raise ValueError("callback-timeout-sec must be greater than 0")

    state = uuid.uuid4().hex
    nonce = uuid.uuid4().hex
    authorize_url = _build_authorize_url(
        gateway_base_url=gateway_base_url,
        redirect_uri=redirect_uri,
        state=state,
        nonce=nonce,
    )
    _save_pending_auth(
        args.ns_bot_home,
        {
            "provider": NUTSTORE_CUSTOM_SLUG,
            "gatewayBaseUrl": gateway_base_url,
            "redirectUri": redirect_uri,
            "state": state,
            "nonce": nonce,
            "model": model_id,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "createdEpoch": time.time(),
        },
    )

    print("Open this URL in your browser and authorize access:", file=sys.stderr)
    print(authorize_url, file=sys.stderr)

    if not args.no_open:
        try:
            webbrowser.open(authorize_url)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] Failed to open browser automatically: {exc}", file=sys.stderr)

    if args.no_listen:
        _print_json(
            {
                "ok": True,
                "status": "pending",
                "authorizeUrl": authorize_url,
                "redirectUri": redirect_uri,
                "next": 'Run `auth paste-redirect --input "<callback-url-or-code>"`',
            }
        )
        return 0

    print(f"Waiting for callback at {redirect_uri} ...", file=sys.stderr)
    callback = _wait_for_local_callback(
        redirect_uri=redirect_uri,
        timeout_seconds=timeout_seconds,
    )
    if callback is None:
        print("Callback capture timed out.", file=sys.stderr)
        print(
            'Run `auth paste-redirect --input "<callback-url-or-code>"`',
            file=sys.stderr,
        )
        return 1

    code, callback_state = callback
    if callback_state is not None and callback_state != state:
        raise ValueError("OAuth state mismatch")

    exchange_payload = _exchange_oidc_code(
        gateway_base_url=gateway_base_url,
        code=code,
        redirect_uri=redirect_uri,
        nonce=nonce,
    )

    database, repositories, _secret_store, provider_service = _build_services(
        args.ns_bot_home
    )
    try:
        bundle = _upsert_nutstore_provider(
            repositories=repositories,
            provider_service=provider_service,
            gateway_base_url=gateway_base_url,
            access_token=str(exchange_payload["access_token"]),
            model_id=model_id,
        )
    finally:
        database.close()

    _clear_pending_auth(args.ns_bot_home)
    _print_json(
        {
            "ok": True,
            "connectionId": str(bundle.get("id") or ""),
            "providerId": _normalize_provider_ref(bundle),
            "modelId": model_id,
            "tokenType": exchange_payload.get("token_type"),
            "expiresIn": exchange_payload.get("expires_in"),
        }
    )
    return 0


def _handle_auth_paste_redirect_command(args: argparse.Namespace) -> int:
    pending = _load_pending_auth(args.ns_bot_home)
    gateway_base_url = _normalize_base_url(
        str(args.gateway_base_url or "").strip()
        or str(pending.get("gatewayBaseUrl") or "")
    )
    redirect_uri = str(pending.get("redirectUri") or "").strip()
    if redirect_uri == "":
        raise ValueError(
            "Pending auth login metadata is invalid. Missing redirect URI."
        )
    expected_state = str(pending.get("state") or "").strip()
    nonce = str(pending.get("nonce") or "").strip()
    if expected_state == "" or nonce == "":
        raise ValueError(
            "Pending auth login metadata is invalid. Run `auth login` again."
        )
    model_id = (
        str(args.model or "").strip()
        or str(pending.get("model") or "").strip()
        or "gpt-5.4"
    )

    code, callback_state = _extract_code_and_state(args.input)
    if callback_state is not None and callback_state != expected_state:
        raise ValueError(
            f"OAuth state mismatch: expected {expected_state}, got {callback_state}"
        )

    exchange_payload = _exchange_oidc_code(
        gateway_base_url=gateway_base_url,
        code=code,
        redirect_uri=redirect_uri,
        nonce=nonce,
    )

    database, repositories, _secret_store, provider_service = _build_services(
        args.ns_bot_home
    )
    try:
        bundle = _upsert_nutstore_provider(
            repositories=repositories,
            provider_service=provider_service,
            gateway_base_url=gateway_base_url,
            access_token=str(exchange_payload["access_token"]),
            model_id=model_id,
        )
    finally:
        database.close()

    _clear_pending_auth(args.ns_bot_home)
    _print_json(
        {
            "ok": True,
            "connectionId": str(bundle.get("id") or ""),
            "providerId": _normalize_provider_ref(bundle),
            "modelId": model_id,
            "tokenType": exchange_payload.get("token_type"),
            "expiresIn": exchange_payload.get("expires_in"),
        }
    )
    return 0


def _handle_auth_command(args: argparse.Namespace) -> int:
    if args.auth_command == "login":
        return _handle_auth_login_command(args)
    if args.auth_command == "paste-redirect":
        return _handle_auth_paste_redirect_command(args)
    raise ValueError(f"Unknown auth command: {args.auth_command}")


def _resolve_run_metadata(
    args: argparse.Namespace,
) -> tuple[RunMetadata, dict[str, Any] | None]:
    explicit_session_id = str(args.session_id or "").strip()
    if explicit_session_id == "":
        return (
            RunMetadata(
                workspace_path=args.workspace_path,
                session_key=args.session_key or None,
            ),
            None,
        )

    database, repositories, _secret_store, _provider_service = _build_services(
        args.ns_bot_home
    )
    try:
        try:
            session = repositories.sessions.get_by_id(explicit_session_id)
        except ValueError as exc:
            raise ValueError(f"Session not found: {explicit_session_id}") from exc
        try:
            workspace = repositories.workspaces.get_by_id(session.workspace_id)
        except ValueError as exc:
            raise ValueError(
                f"Workspace not found for session '{explicit_session_id}'"
            ) from exc
    finally:
        database.close()

    resolved_session_key = str(session.session_key or "").strip()
    if resolved_session_key == "":
        raise ValueError(f"Session '{explicit_session_id}' has empty session_key")

    return (
        RunMetadata(
            workspace_path=workspace.real_path,
            session_key=resolved_session_key,
        ),
        {
            "sessionId": session.id,
            "sessionKey": resolved_session_key,
            "workspaceId": workspace.id,
            "workspacePath": workspace.real_path,
            "activeConnectionId": session.active_connection_id,
            "activeModelId": session.active_model_id,
        },
    )


def _resolve_run_target(
    args: argparse.Namespace,
) -> tuple[RuntimeWorkerConfig, dict[str, Any]]:
    database, repositories, secret_store, provider_service = _build_services(
        args.ns_bot_home
    )
    try:
        direct_mode = any(
            [
                str(args.provider or "").strip(),
                str(args.base_url or "").strip(),
                str(args.api_key or "").strip(),
                str(args.model or "").strip(),
            ]
        )
        explicit_connection_id = str(args.connection_id or "").strip()
        explicit_selected_model = str(args.selected_model_id or "").strip()

        if direct_mode:
            model_id = str(args.model or args.model_id).strip()
            config = RuntimeWorkerConfig(
                model_id=model_id,
                provider=str(args.provider or "custom").strip() or "custom",
                base_url=str(args.base_url or "").strip() or None,
                api_key=str(args.api_key or "").strip() or None,
                model=model_id,
                request_timeout_ms=args.request_timeout_ms,
                ns_bot_home=args.ns_bot_home,
                workspace_path_default=args.workspace_path,
                fd_executable=args.fd_executable or None,
                rg_executable=args.rg_executable or None,
                tool_os_type=args.tool_os_type or None,
                max_steps=args.max_steps,
            )
            return (
                config,
                {
                    "mode": "direct",
                    "connectionId": None,
                    "providerId": str(config.provider or ""),
                    "runtimeProvider": str(config.provider or ""),
                    "modelId": model_id,
                    "baseUrl": config.base_url,
                    "hasApiKey": bool(config.api_key),
                },
            )

        if explicit_connection_id:
            bundle = repositories.providers.get_bundle_by_id(explicit_connection_id)
            if bundle is None:
                raise ValueError(
                    f"Provider connection not found: {explicit_connection_id}"
                )
            selected_model = (
                explicit_selected_model or bundle.connection.preferred_model_id
            )
            if not selected_model:
                options = provider_service.model_options_payload()
                group = _find_target_group(options, provider_ref=explicit_connection_id)
                if group is None:
                    raise ValueError(
                        f"No available models for connection '{explicit_connection_id}'"
                    )
                selected_model = _first_model_id(group)
                if selected_model is None:
                    raise ValueError(
                        f"No available models for connection '{explicit_connection_id}'"
                    )

            secret_payload = secret_store.load_provider_secret(
                bundle.connection.secret_ref
            )
            api_key = secret_payload.api_key if secret_payload is not None else None
            config = RuntimeWorkerConfig(
                model_id=str(selected_model),
                provider=bundle.connection.runtime_provider,
                base_url=bundle.connection.base_url,
                api_key=api_key,
                model=str(selected_model),
                request_timeout_ms=args.request_timeout_ms,
                ns_bot_home=args.ns_bot_home,
                workspace_path_default=args.workspace_path,
                fd_executable=args.fd_executable or None,
                rg_executable=args.rg_executable or None,
                tool_os_type=args.tool_os_type or None,
                max_steps=args.max_steps,
            )
            return (
                config,
                {
                    "mode": "connection",
                    "connectionId": bundle.connection.id,
                    "providerId": bundle.connection.catalog_provider_id
                    or bundle.connection.custom_slug
                    or bundle.connection.runtime_provider,
                    "runtimeProvider": bundle.connection.runtime_provider,
                    "modelId": str(selected_model),
                    "baseUrl": bundle.connection.base_url,
                    "hasApiKey": bool(api_key),
                    "healthStatus": bundle.connection.health_status,
                },
            )

        options = provider_service.model_options_payload()
        default_selection = options.get("defaultSelection")
        if not isinstance(default_selection, dict):
            raise ValueError(
                "No default provider/model available. Configure and validate a provider first."
            )
        connection_id = str(default_selection.get("connectionId") or "")
        model_id = str(default_selection.get("modelId") or "")
        if connection_id == "" or model_id == "":
            raise ValueError("Default selection is invalid")
        bundle = repositories.providers.get_bundle_by_id(connection_id)
        if bundle is None:
            raise ValueError(f"Default provider connection not found: {connection_id}")
        secret_payload = secret_store.load_provider_secret(bundle.connection.secret_ref)
        api_key = secret_payload.api_key if secret_payload is not None else None
        config = RuntimeWorkerConfig(
            model_id=model_id,
            provider=bundle.connection.runtime_provider,
            base_url=bundle.connection.base_url,
            api_key=api_key,
            model=model_id,
            request_timeout_ms=args.request_timeout_ms,
            ns_bot_home=args.ns_bot_home,
            workspace_path_default=args.workspace_path,
            fd_executable=args.fd_executable or None,
            rg_executable=args.rg_executable or None,
            tool_os_type=args.tool_os_type or None,
            max_steps=args.max_steps,
        )
        return (
            config,
            {
                "mode": "default-selection",
                "connectionId": bundle.connection.id,
                "providerId": bundle.connection.catalog_provider_id
                or bundle.connection.custom_slug
                or bundle.connection.runtime_provider,
                "runtimeProvider": bundle.connection.runtime_provider,
                "modelId": model_id,
                "baseUrl": bundle.connection.base_url,
                "hasApiKey": bool(api_key),
                "healthStatus": bundle.connection.health_status,
            },
        )
    finally:
        database.close()


def _handle_run_command(args: argparse.Namespace) -> int:
    config, resolved = _resolve_run_target(args)
    metadata, resolved_session = _resolve_run_metadata(args)
    effective_workspace_path = metadata.workspace_path or args.workspace_path
    if args.diagnose:
        _print_json(
            {
                "resolved": resolved,
                "resolvedSession": resolved_session,
                "runtime": {
                    "modelId": config.model_id,
                    "provider": config.provider,
                    "model": config.model,
                    "baseUrl": config.base_url,
                    "hasApiKey": bool(config.api_key),
                    "requestTimeoutMs": config.request_timeout_ms,
                    "maxSteps": config.max_steps,
                    "fdExecutable": config.fd_executable,
                    "rgExecutable": config.rg_executable,
                },
                "workspacePath": effective_workspace_path,
                "sessionKey": metadata.session_key,
            }
        )
        return 0

    auth_context = {
        "uid": "cli-user",
        "tid": "cli-team",
        "exp_epoch": 0,
    }

    print("[*] Initializing CodeAgentRuntimeService", file=sys.stderr)
    print(f"[*] Workspace: {effective_workspace_path}", file=sys.stderr)
    model_disp = config.model or config.model_id
    print(f"[*] Model: {model_disp} (Provider: {config.provider})", file=sys.stderr)
    print(f"[*] Base URL: {config.base_url}", file=sys.stderr)

    service = CodeAgentRuntimeService(config)
    print(f"\n[*] Processing user input: {args.user_input}", file=sys.stderr)
    print("-" * 50, file=sys.stderr)
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


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    effective_argv = _ensure_run_compat(
        list(argv) if argv is not None else sys.argv[1:]
    )
    args = parser.parse_args(effective_argv)

    try:
        if args.command == "providers":
            return _handle_providers_command(args)
        if args.command == "models":
            return _handle_models_command(args)
        if args.command == "auth":
            return _handle_auth_command(args)
        if args.command == "init":
            return _handle_init_command(args)
        if args.command == "workspaces":
            return _handle_workspaces_command(args)
        if args.command == "sessions":
            return _handle_sessions_command(args)
        if args.command == "run":
            return _handle_run_command(args)
        if args.command == "help":
            return _print_help_for_topic(parser, list(args.topic or []))
        raise ValueError(f"Unknown command: {args.command}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n[!] Error during execution: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
