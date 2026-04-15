from __future__ import annotations

import asyncio
import os

from nsbot_sidecar.api.api_server import ApiServerConfig, DEFAULT_HOST, DEFAULT_PORT, create_app
from nsbot_sidecar.api.acp_session import AcpJsonRpcSession, StdioJsonRpcTransport
from nsbot_sidecar.infrastructure.client_config import load_or_create_client_config


def _config_from_env() -> ApiServerConfig:
    host = os.environ.get("NS_BOT_HOST", DEFAULT_HOST)
    port = int(os.environ.get("NS_BOT_PORT", str(DEFAULT_PORT)))
    ns_bot_home_value = os.environ.get("NS_BOT_HOME")
    client_config = load_or_create_client_config(
        ns_bot_home_value,
        host=host,
        port=port,
    )
    return ApiServerConfig(
        host=host,
        port=port,
        auth_header_value=client_config.auth_header_value,
        ns_bot_home=ns_bot_home_value,
        fd_executable=os.environ.get("NSBOT_FD_EXECUTABLE") or None,
        rg_executable=os.environ.get("NSBOT_RG_EXECUTABLE") or None,
    )


async def _run_stdio() -> None:
    app = create_app(_config_from_env())
    session = AcpJsonRpcSession(StdioJsonRpcTransport(), app.state)
    await session.run()


def main() -> int:
    asyncio.run(_run_stdio())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
