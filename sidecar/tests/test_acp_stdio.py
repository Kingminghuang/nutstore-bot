from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from acp import spawn_agent_process, text_block
from acp.interfaces import Client


async def _write_jsonrpc_payload(
    proc: asyncio.subprocess.Process,
    payload: dict,
) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    await proc.stdin.drain()


async def _read_jsonrpc_message(
    proc: asyncio.subprocess.Process,
    *,
    timeout: float = 15,
) -> dict:
    assert proc.stdout is not None
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
    if not line:
        raise AssertionError("ACP stdio process closed before sending message")
    return json.loads(line)


async def _send_jsonrpc_request(
    proc: asyncio.subprocess.Process,
    request_id: int,
    method: str,
    params: dict,
) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }
    await _write_jsonrpc_payload(proc, payload)

    while True:
        message = await _read_jsonrpc_message(proc)
        if message.get("id") == request_id:
            return message


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
        return {"outcome": {"outcome": "selected", "optionId": "approved"}}


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
                await conn.authenticate(method_id="USE_OPENAI")

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

    def test_cli_root_acp_mode_supports_prompt_round_trip(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="acp-cli-stdio-"))
        workspace = temp_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        async def _run() -> None:
            client = _RecordingClient()
            env = {
                **os.environ,
                "NS_BOT_HOME": str(temp_dir),
            }
            async with spawn_agent_process(
                client,
                sys.executable,
                "-m",
                "nsbot_sidecar.cli",
                "--acp",
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
                await conn.authenticate(method_id="USE_OPENAI")

                session = await conn.new_session(cwd=str(workspace))

                try:
                    await conn.prompt(
                        session_id=session.session_id,
                        prompt=[text_block("ping from cli acp mode")],
                    )
                except Exception:
                    pass

                self.assertTrue(
                    any(
                        sid == session.session_id
                        and str((update.get("sessionUpdate") or "")) == "user_message_chunk"
                        and str((update.get("content") or {}).get("text") or "")
                        == "ping from cli acp mode"
                        for sid, update in client.session_updates
                    )
                )

        asyncio.run(_run())

    def test_raw_jsonrpc_session_requests_accept_frontend_bridge_shape(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="acp-stdio-raw-"))
        workspace = temp_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        async def _run() -> None:
            sidecar_src = str(Path(__file__).resolve().parents[1] / "src")
            env = {
                **os.environ,
                "NSBOT_ACP_TRANSPORT": "stdio",
                "NS_BOT_HOME": str(temp_dir),
                "PYTHONPATH": sidecar_src
                if not os.environ.get("PYTHONPATH")
                else f"{sidecar_src}:{os.environ['PYTHONPATH']}",
            }
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "nsbot_sidecar.api.acp_stdio",
                cwd=str(workspace),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                init = await _send_jsonrpc_request(
                    proc,
                    1,
                    "initialize",
                    {
                        "protocolVersion": 1,
                        "clientCapabilities": {
                            "fs": {"readTextFile": False, "writeTextFile": False},
                            "terminal": False,
                        },
                    },
                )
                self.assertEqual(init["result"]["protocolVersion"], 1)

                provider = await _send_jsonrpc_request(
                    proc,
                    2,
                    "_nsbot/provider/create",
                    {
                        "kind": "builtin",
                        "catalogProviderId": "openai",
                        "displayName": "OpenAI",
                        "apiKey": "sk-test",
                        "preferredModelId": "gpt-5.4",
                    },
                )
                self.assertIn("id", provider["result"])

                authenticate = await _send_jsonrpc_request(
                    proc,
                    3,
                    "authenticate",
                    {"methodId": "USE_OPENAI"},
                )
                self.assertIn("result", authenticate)

                session = await _send_jsonrpc_request(
                    proc,
                    4,
                    "session/new",
                    {"cwd": str(workspace), "mcpServers": []},
                )
                session_id = session["result"].get("sessionId")
                self.assertTrue(session_id)

                loaded = await _send_jsonrpc_request(
                    proc,
                    5,
                    "session/load",
                    {
                        "cwd": str(workspace),
                        "sessionId": session_id,
                        "mcpServers": [],
                    },
                )
                self.assertIn("configOptions", loaded["result"])
            finally:
                if proc.stdin is not None:
                    proc.stdin.close()
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

        asyncio.run(_run())

if __name__ == "__main__":
    unittest.main()
