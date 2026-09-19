import asyncio
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.memory.client import MCPMemoryClient


def _field(value, name):
    normalized = name.lower().replace("_", "")
    return next(
        (item for key, item in value.items()
         if str(key).lower().replace("_", "") == normalized),
        None,
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_supervisor_client_calls_the_real_memory_mcp_process_tool(tmp_path):
    async def scenario():
        port = _free_port()
        database = tmp_path / "memory.db"
        environment = {
            **os.environ,
            "GRAMS_DB_PATH": str(database),
            "GRAMS_MCP_ADDR": f"127.0.0.1:{port}",
        }
        server = subprocess.Popen(
            ["go", "run", "./cmd/server"],
            cwd=Path(__file__).parents[1] / "memory-mcp",
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        client = MCPMemoryClient(f"http://127.0.0.1:{port}")
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    tools = await client.list_tools()
                    break
                except Exception:
                    await asyncio.sleep(0.1)
            else:
                output = server.stdout.read() if server.stdout else ""
                raise AssertionError(f"Memory MCP did not start: {output}")

            assert "process_get_active" in {tool["name"] for tool in tools}
            project_id = await client.ensure_session_project("integration-session")
            assert await client.get_active_process(project_id) is None

            process = await client.create_process({
                "project_id": project_id,
                "name": "process_001",
                "description": "integration test",
            })
            active = await client.get_active_process(project_id)
            assert _field(active, "id") == _field(process, "id")
            assert _field(active, "project_id") == project_id
            assert _field(active, "status") == "ACTIVE"

            await client.close_process(_field(process, "id"), "SUCCEEDED")
            assert await client.get_active_process(project_id) is None
        finally:
            await client.aclose()
            os.killpg(server.pid, signal.SIGTERM)
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait()

    asyncio.run(scenario())
