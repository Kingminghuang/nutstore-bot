from __future__ import annotations

from contextlib import asynccontextmanager
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from attachment_store import AttachmentStore
from auth import (
    LocalAuthConfig,
    auth_header_dependency,
    generate_local_auth_token,
    is_exempt_path,
    validate_bearer_token,
)
from discovery import ServiceDiscovery, nsbot_home, write_service_discovery
from provider_service import ProviderService
from redaction import install_log_redaction_filter, redact_sensitive
from run_cancellation import RunCancellationRegistry
from run_event_store import RunEventStore
from run_service import RunRequestFailed, RunService
from repositories import create_repositories
from session_service import SessionService
from secret_store import LocalSecretStore
from storage import connect_database


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


@dataclass(frozen=True)
class ApiServerConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    token: str | None = None
    ns_bot_home: str | None = None
    version: str = "0.1.0"


def ensure_local_host(host: str) -> None:
    if host not in {DEFAULT_HOST, "localhost"}:
        raise ValueError("Sidecar host must bind to localhost")


def create_app(config: ApiServerConfig | None = None) -> FastAPI:
    cfg = config or ApiServerConfig()
    ensure_local_host(cfg.host)
    token = cfg.token or generate_local_auth_token()
    auth_config = LocalAuthConfig(token=token)
    database = connect_database(cfg.ns_bot_home)
    repositories = create_repositories(database)
    provider_service = ProviderService(
        repositories=repositories.providers,
        secret_store=LocalSecretStore(cfg.ns_bot_home),
    )
    session_service = SessionService(
        workspaces=repositories.workspaces,
        sessions=repositories.sessions,
        messages=repositories.messages,
        attachments=repositories.attachments,
        attachment_store=AttachmentStore(cfg.ns_bot_home),
    )
    run_service = RunService(
        workspaces=repositories.workspaces,
        sessions=repositories.sessions,
        providers=repositories.providers,
        runs=repositories.runs,
        run_steps=repositories.run_steps,
        session_service=session_service,
        attachments=repositories.attachments,
        secret_store=LocalSecretStore(cfg.ns_bot_home),
        event_store=RunEventStore(),
        cancellation_registry=RunCancellationRegistry(),
        ns_bot_home=cfg.ns_bot_home,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            db = getattr(app.state, "database", None)
            if isinstance(db, sqlite3.Connection):
                db.close()

    app = FastAPI(title="Nutstore Bot Sidecar", version=cfg.version, lifespan=lifespan)
    app.state.api_server_config = cfg
    app.state.local_auth = auth_config
    app.state.database = database
    app.state.repositories = repositories
    app.state.provider_service = provider_service
    app.state.session_service = session_service
    app.state.run_service = run_service
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

    @app.middleware("http")
    async def localhost_auth_middleware(request: Request, call_next):  # type: ignore[override]
        if not is_exempt_path(request.url.path, auth_config.exempt_paths):
            try:
                validate_bearer_token(
                    request.headers.get("Authorization"), auth_config.token
                )
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code, content={"detail": exc.detail}
                )

        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "service": "sidecar",
            "version": cfg.version,
        }

    @app.get("/auth/check")
    def auth_check(_: str | None = Depends(auth_header_dependency)) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/provider-catalog")
    def get_provider_catalog() -> dict[str, object]:
        return provider_service.catalog_payload()

    @app.get("/providers")
    def get_providers() -> dict[str, list[dict[str, object]]]:
        return provider_service.list_connections_payload()

    @app.get("/model-options")
    def get_model_options() -> dict[str, object]:
        return provider_service.model_options_payload()

    @app.get("/workspaces")
    def get_workspaces() -> dict[str, list[dict[str, object]]]:
        return session_service.list_workspaces_payload()

    @app.post("/workspaces")
    def create_workspace(payload: dict[str, object]) -> dict[str, object]:
        return session_service.create_workspace(payload)

    @app.patch("/workspaces/{workspace_id}")
    def update_workspace(
        workspace_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        return session_service.update_workspace(workspace_id, payload)

    @app.delete("/workspaces/{workspace_id}", status_code=204)
    def delete_workspace(workspace_id: str) -> None:
        session_service.delete_workspace(workspace_id)

    @app.get("/workspaces/{workspace_id}/sessions")
    def get_workspace_sessions(workspace_id: str) -> dict[str, list[dict[str, object]]]:
        return session_service.list_sessions_payload(workspace_id)

    @app.post("/workspaces/{workspace_id}/sessions")
    def create_workspace_session(
        workspace_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        return session_service.create_session(workspace_id, payload)

    @app.patch("/sessions/{session_id}")
    def update_session(
        session_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        return session_service.update_session(session_id, payload)

    @app.get("/sessions/{session_id}/messages")
    def get_session_messages(
        session_id: str,
        limit: int | None = Query(default=None, ge=1),
        before_sequence: int | None = Query(default=None, alias="beforeSequence", ge=1),
    ) -> dict[str, object]:
        return session_service.list_messages_payload(
            session_id,
            limit=limit,
            before_sequence=before_sequence,
        )

    @app.post("/sessions/{session_id}/messages")
    def create_session_message(
        session_id: str,
        payload: dict[str, object],
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        return session_service.append_message(
            session_id, payload, background_tasks=background_tasks
        )

    @app.get("/sessions/{session_id}/attachments")
    def get_session_attachments(session_id: str) -> dict[str, list[dict[str, object]]]:
        return session_service.list_attachments_payload(session_id)

    @app.post("/sessions/{session_id}/attachments")
    async def create_session_attachment(
        session_id: str,
        file: UploadFile = File(...),
    ) -> dict[str, object]:
        payload = await file.read()
        return session_service.create_attachment(
            session_id,
            file_name=file.filename or "attachment",
            mime_type=file.content_type or "application/octet-stream",
            payload=payload,
        )

    @app.delete("/sessions/{session_id}/attachments/{attachment_id}", status_code=204)
    def delete_session_attachment(session_id: str, attachment_id: str) -> None:
        session_service.delete_attachment(session_id, attachment_id)

    @app.post("/runs")
    def create_run(
        payload: dict[str, object], background_tasks: BackgroundTasks, request: Request
    ):
        service = request.app.state.run_service
        try:
            return service.create_run(payload, background_tasks=background_tasks)
        except RunRequestFailed as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content=redact_sensitive(exc.payload),
            )

    @app.get("/runs/{run_id}/events")
    def get_run_events(
        run_id: str, request: Request, after: int = 0
    ) -> StreamingResponse:
        service = request.app.state.run_service
        try:
            service.runs.get_by_id(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc

        last_sequence = [after]

        def wrapped_stream():
            idle_cycles = 0
            while True:
                envelopes = service.list_run_events(run_id, last_sequence[0])
                if envelopes:
                    idle_cycles = 0
                for envelope in envelopes:
                    last_sequence[0] = int(envelope.data.get("sequence", 0))
                    yield f"id: {envelope.id}\n"
                    yield f"event: {envelope.event}\n"
                    yield f"data: {json.dumps(envelope.data, ensure_ascii=False)}\n\n"

                if service.is_run_event_stream_closed(run_id):
                    break

                idle_cycles += 1
                if idle_cycles >= 20:
                    yield ": keepalive\n\n"
                    idle_cycles = 0
                time.sleep(0.05)

        return StreamingResponse(
            wrapped_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.get("/runs/{run_id}/steps")
    def get_run_steps(
        run_id: str, request: Request
    ) -> dict[str, list[dict[str, object]]]:
        service = request.app.state.run_service
        try:
            return service.list_run_steps_payload(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc

    @app.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str, request: Request) -> dict[str, object]:
        service = request.app.state.run_service
        return service.cancel_run(run_id)

    @app.post("/providers")
    def create_provider(payload: dict[str, object]) -> dict[str, object]:
        return provider_service.create_provider(payload)

    @app.patch("/providers/{provider_id}")
    def update_provider(
        provider_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        return provider_service.update_provider(provider_id, payload)

    @app.delete("/providers/{provider_id}", status_code=204)
    def delete_provider(provider_id: str) -> None:
        provider_service.delete_provider(provider_id)

    @app.post("/providers/{provider_id}/validate")
    def validate_provider(
        provider_id: str, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        return provider_service.validate_provider(provider_id, payload)

    return app


def publish_service_discovery(
    config: ApiServerConfig, token: str | None = None
) -> Path:
    ensure_local_host(config.host)
    effective_token = token or config.token or generate_local_auth_token()
    discovery = ServiceDiscovery(
        base_url=f"http://{config.host}:{config.port}",
        port=config.port,
        token=effective_token,
        pid=os.getpid(),
    )
    return write_service_discovery(discovery, str(nsbot_home(config.ns_bot_home)))


def main() -> int:
    import uvicorn

    config = ApiServerConfig(
        host=os.environ.get("NS_BOT_HOST", DEFAULT_HOST),
        port=int(os.environ.get("NS_BOT_PORT", str(DEFAULT_PORT))),
        token=os.environ.get("NS_BOT_TOKEN") or generate_local_auth_token(),
        ns_bot_home=os.environ.get("NS_BOT_HOME"),
    )

    publish_service_discovery(config, config.token)
    uvicorn.run(create_app(config), host=config.host, port=config.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
