import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.agent.nodes.load_process_context import make_load_process_context


class FakeMemory:
    async def get_process(self, process_id):
        assert process_id == "process-1"
        return {
            "id": process_id,
            "project_id": "project-1",
            "key_id": "key-1",
            "name": "process_001",
            "status": "ACTIVE",
        }

    async def get_manifest(self):
        return {
            "projects": [{
                "id": "project-1",
                "keys": [{
                    "id": "key-1",
                    "name": "process_001",
                    "categories": [
                        {"id": "summary-1", "name": "SUMMARY"},
                        {"id": "strategy-1", "name": "STRATEGY"},
                        {"id": "evidence-1", "name": "EVIDENCE"},
                    ],
                }],
            }],
        }

    async def search(self, query="", **filters):
        return [{
            "id": filters["category_id"],
            "category_id": filters["category_id"],
            "updated_at": "2026-09-17T12:00:00+00:00",
        }]

    async def neighbors(self, memory_id, **filters):
        return {"nodes": [], "edges": []}

    async def list_processes(self, project_id):
        return [{
            "id": "process-1",
            "project_id": project_id,
            "key_id": "key-1",
            "name": "process_001",
            "status": "ACTIVE",
        }]


def test_loads_process_and_memory_categories():
    async def scenario():
        node = make_load_process_context(FakeMemory())

        result = await node({
            "project_id": "project-1",
            "active_process_id": "process-1",
        })

        context = result["process_context"]
        assert context["process"]["id"] == "process-1"
        assert set(context["categories"]) == {"STRATEGY", "EVIDENCE", "SUMMARY"}
        assert all(len(items) == 1 for items in context["categories"].values())
        assert len(context["recent_changes"]) == 3

    asyncio.run(scenario())


def test_requires_process_identity():
    async def scenario():
        node = make_load_process_context(FakeMemory())

        try:
            await node({"project_id": "project-1"})
        except ValueError as error:
            assert str(error) == "project_id and active_process_id are required to load process context"
        else:
            raise AssertionError("expected missing process identity error")

    asyncio.run(scenario())
