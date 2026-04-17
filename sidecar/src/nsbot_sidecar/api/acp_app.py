from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from nsbot_sidecar.api.redaction import install_log_redaction_filter, redact_sensitive
from nsbot_sidecar.application.provider_service import ProviderService
from nsbot_sidecar.application.session_service import SessionService
from nsbot_sidecar.application.timeline_service import TimelineService
from nsbot_sidecar.infrastructure.attachment_store import AttachmentStore
from nsbot_sidecar.infrastructure.repositories import create_repositories
from nsbot_sidecar.infrastructure.secret_store import LocalSecretStore
from nsbot_sidecar.infrastructure.storage import connect_database
from nsbot_sidecar.runtime.workspace_sidecar_indexer import WorkspaceSidecarIndexer


@dataclass(frozen=True)
class AcpAppConfig:
    ns_bot_home: str | None = None
    fd_executable: str | None = None
    rg_executable: str | None = None
    version: str = "0.1.0"


def create_acp_app(config: AcpAppConfig | None = None) -> FastAPI:
    cfg = config or AcpAppConfig()
    database = connect_database(cfg.ns_bot_home)
    repositories = create_repositories(database)
    secret_store = LocalSecretStore(cfg.ns_bot_home)
    provider_service = ProviderService(
        repositories=repositories.providers,
        secret_store=secret_store,
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

    app = FastAPI(title="NutstoreBot ACP Runtime", version=cfg.version, lifespan=lifespan)
    app.state.acp_app_config = cfg
    app.state.database = database
    app.state.repositories = repositories
    app.state.provider_service = provider_service
    app.state.session_service = session_service
    app.state.secret_store = secret_store
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
            status_code=exc.status_code,
            content=redact_sensitive(payload),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=redact_sensitive({"detail": str(exc)}),
        )

    return app