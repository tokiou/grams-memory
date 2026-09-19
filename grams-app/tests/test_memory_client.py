import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.memory.client import MCPMemoryClient


def test_session_hierarchy_uses_root_session_as_project():
    async def scenario():
        calls = []

        class FakeMemory(MCPMemoryClient):
            def __init__(self):
                pass

            async def _call_tool(self, name, arguments):
                calls.append((name, arguments))
                if name == "project_list":
                    return []
                if name == "project_create":
                    return {"id": "project-session"}
                if name == "key_list":
                    return []
                if name == "key_create":
                    return {"id": f"key-{arguments['name']}"}
                if name == "category_list":
                    return []
                if name == "category_create":
                    return {"id": f"category-{arguments['name']}"}
                raise AssertionError(name)

        category_id = await FakeMemory().ensure_session_hierarchy("ses_example")

        assert category_id == "category-progress"
        assert calls[0] == ("project_list", {})
        assert calls[1] == (
            "project_create",
            {
                "name": "ses_example",
                "description": "OpenCode session memory for ses_example",
            },
        )
        assert [arguments["name"] for name, arguments in calls if name == "key_create"] == [
            "objective",
            "execution",
            "results",
        ]
        assert len([name for name, _ in calls if name == "category_create"]) == 9

    asyncio.run(scenario())


def test_get_active_process_uses_the_canonical_mcp_tool():
    async def scenario():
        calls = []

        class FakeMemory(MCPMemoryClient):
            def __init__(self):
                pass

            async def _call_tool(self, name, arguments):
                calls.append((name, arguments))
                return {"id": "process-1", "project_id": "project-1", "status": "ACTIVE"}

        process = await FakeMemory().get_active_process("project-1")

        assert process["id"] == "process-1"
        assert calls == [("process_get_active", {"id": "project-1"})]

    asyncio.run(scenario())


def test_get_active_process_rejects_an_empty_project_id():
    async def scenario():
        class FakeMemory(MCPMemoryClient):
            def __init__(self):
                pass

        try:
            await FakeMemory().get_active_process(" ")
        except ValueError as error:
            assert str(error) == "project_id must not be empty"
        else:
            raise AssertionError("expected empty project id to be rejected")

    asyncio.run(scenario())


def test_get_active_process_rejects_an_invalid_mcp_process():
    async def scenario():
        class FakeMemory(MCPMemoryClient):
            def __init__(self):
                pass

            async def _call_tool(self, name, arguments):
                return {"id": "process-1", "project_id": "other-project", "status": "ACTIVE"}

        with pytest.raises(RuntimeError, match="wrong project"):
            await FakeMemory().get_active_process("project-1")

    asyncio.run(scenario())


def test_mcp_handshake_rejects_a_server_missing_process_tool():
    async def scenario():
        async def handler(request):
            payload = json.loads(request.content)
            if payload["method"] == "notifications/initialized":
                return httpx.Response(202)
            if payload["method"] == "initialize":
                return httpx.Response(200, json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {},
                })
            if payload["method"] == "tools/list":
                return httpx.Response(200, json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"tools": [{"name": "project_list"}]},
                })
            raise AssertionError(payload["method"])

        client = MCPMemoryClient("http://mcp")
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(RuntimeError, match="missing required tool process_get_active"):
                await client.list_tools()
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_tool_error_keeps_the_canonical_tool_name():
    async def scenario():
        class FakeMemory(MCPMemoryClient):
            def __init__(self):
                pass

            async def _ensure_initialized(self):
                return None

            async def _request(self, method, params=None):
                raise RuntimeError("Memory MCP tools/call failed (-32602): unknown tool")

        with pytest.raises(RuntimeError, match="process_get_active.*unknown tool"):
            await FakeMemory().get_active_process("project-1")

    asyncio.run(scenario())
