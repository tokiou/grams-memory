import asyncio
import sys
from pathlib import Path

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
