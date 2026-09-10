import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "supervisor"))

from supervisor.memory.client import MCPMemoryClient
from supervisor.agent.nodes.memory_operation import make_memory_operation_node


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


def test_session_memory_operations_reject_foreign_memory_ids():
    async def scenario():
        class FakeMemory:
            async def get(self, memory_id):
                return {"id": memory_id, "category_id": "category-other"}

            async def update(self, memory_id, arguments):
                raise AssertionError("foreign memory must be rejected before update")

        node = make_memory_operation_node(FakeMemory())
        state = {
            "root_session_id": "ses_example",
            "session_memory_category_id": "category-progress",
            "memory_manifest": {
                "projects": [{
                    "name": "ses_example",
                    "keys": [{"categories": [{"id": "category-progress"}]}],
                }],
            },
            "memory_operation": {"operation": "update", "arguments": {"id": "memory-other", "content": "bad"}},
        }
        with pytest.raises(ValueError, match="current memory scope"):
            await node(state)

    asyncio.run(scenario())


def test_session_memory_operations_fail_closed_without_hierarchy():
    async def scenario():
        class FakeMemory:
            async def search(self, query, **filters):
                raise AssertionError("unscoped search must not be dispatched")

        node = make_memory_operation_node(FakeMemory())
        with pytest.raises(ValueError, match="hierarchy is unavailable"):
            await node({
                "root_session_id": "ses_example",
                "memory_operation": {"operation": "search", "arguments": {"query": "task"}},
            })

    asyncio.run(scenario())
