from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from auth import (
    LocalAuthConfig,
    auth_header_dependency,
    generate_local_auth_token,
    is_exempt_path,
    validate_bearer_token,
)
from discovery import ServiceDiscovery, nsbot_home, write_service_discovery
from provider_service import ProviderService
from repositories import create_repositories
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

    app = FastAPI(title="Nutstore Bot Sidecar", version=cfg.version)
    app.state.api_server_config = cfg
    app.state.local_auth = auth_config
    app.state.database = database
    app.state.repositories = repositories
    app.state.provider_service = provider_service

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
    def get_provider_catalog() -> dict[str, list[dict[str, object]]]:
        return provider_service.catalog_payload()

    @app.get("/providers")
    def get_providers() -> dict[str, list[dict[str, object]]]:
        return provider_service.list_connections_payload()

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

    @app.on_event("shutdown")
    def close_database() -> None:
        db = getattr(app.state, "database", None)
        if isinstance(db, sqlite3.Connection):
            db.close()

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
