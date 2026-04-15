from __future__ import annotations

from contextlib import asynccontextmanager
import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from nsbot_sidecar.infrastructure.attachment_store import AttachmentStore
from nsbot_sidecar.infrastructure.client_config import load_or_create_client_config
from nsbot_sidecar.api.discovery import ServiceDiscovery, nsbot_home, write_service_discovery
from nsbot_sidecar.application.provider_service import ProviderService
from nsbot_sidecar.api.redaction import install_log_redaction_filter, redact_sensitive
from nsbot_sidecar.infrastructure.repositories import create_repositories
from nsbot_sidecar.application.session_service import SessionService
from nsbot_sidecar.infrastructure.secret_store import LocalSecretStore
from nsbot_sidecar.infrastructure.storage import connect_database
from nsbot_sidecar.application.timeline_service import TimelineService
from nsbot_sidecar.runtime.workspace_sidecar_indexer import WorkspaceSidecarIndexer


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18765
LOCAL_CORS_ORIGINS = (
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:13000",
    "http://127.0.0.1:13000",
)


@dataclass(frozen=True)
class ApiServerConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    auth_header_value: str | None = None
    ns_bot_home: str | None = None
    fd_executable: str | None = None
    rg_executable: str | None = None
    version: str = "0.1.0"


def ensure_local_host(host: str) -> None:
    if host not in {DEFAULT_HOST, "localhost"}:
        raise ValueError("NSBot host must bind to localhost")


def create_app(config: ApiServerConfig | None = None) -> FastAPI:
    cfg = config or ApiServerConfig()
    ensure_local_host(cfg.host)
    database = connect_database(cfg.ns_bot_home)
    repositories = create_repositories(database)
    provider_service = ProviderService(
        repositories=repositories.providers,
        secret_store=LocalSecretStore(cfg.ns_bot_home),
    )
    session_service = SessionService(
        workspaces=repositories.workspaces,
        sessions=repositories.sessions,
        attachments=repositories.attachments,
        draft_attachments=repositories.draft_attachments,
        attachment_store=AttachmentStore(cfg.ns_bot_home),
        timeline_service=TimelineService(
            sessions=repositories.sessions,
            acp_event_log=repositories.acp_event_log,
        ),
        workspace_sidecar_indexer=WorkspaceSidecarIndexer(),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            db = getattr(app.state, "database", None)
            if hasattr(db, "close"):
                db.close()

    app = FastAPI(title="NutstoreBot NSBot", version=cfg.version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(LOCAL_CORS_ORIGINS),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.api_server_config = cfg
    app.state.database = database
    app.state.repositories = repositories
    app.state.provider_service = provider_service
    app.state.session_service = session_service
    app.state.secret_store = LocalSecretStore(cfg.ns_bot_home)
    install_log_redaction_filter()

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        sanitized_errors: list[dict[str, object]] = []
        for error in exc.errors():
            sanitized: dict[str, object] = {
                "type": error.get("type"),
                "loc": error.get("loc"),
                "msg": error.get("msg"),
            }
            ctx = error.get("ctx")
            if isinstance(ctx, dict) and ctx:
                sanitized["ctx"] = ctx
            sanitized_errors.append(sanitized)

        return JSONResponse(
            status_code=422,
            content=redact_sensitive({"detail": sanitized_errors}),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        detail = exc.detail
        payload = detail if isinstance(detail, dict) else {"detail": detail}
        return JSONResponse(
            status_code=exc.status_code, content=redact_sensitive(payload)
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=redact_sensitive({"detail": str(exc)}),
        )

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "service": "sidecar",
            "version": cfg.version,
        }

    return app


def publish_service_discovery(
    config: ApiServerConfig,
) -> Path:
    ensure_local_host(config.host)
    auth_header_value = (config.auth_header_value or "").strip()
    if auth_header_value.lower().startswith("bearer "):
        token = auth_header_value[7:].strip()
    else:
        token = auth_header_value
    discovery = ServiceDiscovery(
        base_url=f"http://{config.host}:{config.port}",
        port=config.port,
        token=token,
        pid=os.getpid(),
    )
    return write_service_discovery(discovery, str(nsbot_home(config.ns_bot_home)))


def main() -> int:
    if os.environ.get("NSBOT_ACP_TRANSPORT", "").strip().lower() == "stdio":
        from nsbot_sidecar.api.acp_stdio import main as acp_stdio_main

        return acp_stdio_main()

    import uvicorn

    host = os.environ.get("NS_BOT_HOST", DEFAULT_HOST)
    port = int(os.environ.get("NS_BOT_PORT", str(DEFAULT_PORT)))
    ns_bot_home_value = os.environ.get("NS_BOT_HOME")
    client_config = load_or_create_client_config(
        ns_bot_home_value,
        host=host,
        port=port,
    )

    config = ApiServerConfig(
        host=host,
        port=port,
        auth_header_value=client_config.auth_header_value,
        ns_bot_home=ns_bot_home_value,
        fd_executable=os.environ.get("NSBOT_FD_EXECUTABLE") or None,
        rg_executable=os.environ.get("NSBOT_RG_EXECUTABLE") or None,
    )

    publish_service_discovery(config)
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
