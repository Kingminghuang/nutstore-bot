from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest

from acp import spawn_agent_process, text_block
from acp.interfaces import Client


class _RecordingClient(Client):
    def __init__(self) -> None:
        self.session_updates: list[tuple[str, dict]] = []

    async def session_update(self, session_id, update, **kwargs):  # type: ignore[override]
        del kwargs
        payload = update if isinstance(update, dict) else update.model_dump(by_alias=True)
        self.session_updates.append((session_id, payload))

    async def request_permission(
        self, options, session_id, tool_call, **kwargs
    ):  # type: ignore[override]
        del options, session_id, tool_call, kwargs
        return {"outcome": {"outcome": "selected", "optionId": "allow-once"}}


class AcpStdioIntegrationTests(unittest.TestCase):
    def test_stdio_handshake_and_session_update_stream(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="acp-stdio-"))
        workspace = temp_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        async def _run() -> None:
            client = _RecordingClient()
            env = {
                **os.environ,
                "NSBOT_ACP_TRANSPORT": "stdio",
                "NS_BOT_HOME": str(temp_dir),
            }
            async with spawn_agent_process(
                client,
                sys.executable,
                "-m",
                "nsbot_sidecar.api.acp_stdio",
                env=env,
                cwd=str(workspace),
            ) as (conn, _proc):
                init = await conn.initialize(protocol_version=1)
                self.assertEqual(init.protocol_version, 1)

                await conn.ext_method(
                    "nsbot/provider/create",
                    {
                        "kind": "builtin",
                        "catalogProviderId": "openai",
                        "displayName": "OpenAI",
                        "apiKey": "sk-test",
                        "preferredModelId": "gpt-5.4",
                    },
                )

                await conn.ext_method("nsbot/provider/catalog", {})

                session = await conn.new_session(cwd=str(workspace))
                sessions = await conn.list_sessions(cwd=str(workspace))
                self.assertTrue(
                    any(item.session_id == session.session_id for item in sessions.sessions)
                )

                timeline = await conn.ext_method(
                    "nsbot/timeline/list",
                    {"sessionId": session.session_id, "limit": 20},
                )
                self.assertIn("events", timeline)

                try:
                    await conn.prompt(
                        session_id=session.session_id,
                        prompt=[text_block("ping from stdio integration")],
                    )
                except Exception:
                    pass

                self.assertTrue(
                    any(
                        sid == session.session_id
                        and str((update.get("sessionUpdate") or "")) == "user_message_chunk"
                        for sid, update in client.session_updates
                    )
                )

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
