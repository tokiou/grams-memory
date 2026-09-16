import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.agent.nodes.ensure_active_process import make_ensure_active_process


class FakeMemory:
    def __init__(self, *, active=None, processes=None):
        self.active = active
        self.processes = processes or []
        self.calls = []

    async def ensure_session_project(self, root_session_id):
        self.calls.append(("ensure_session_project", root_session_id))
        return "project-1"

    async def get_active_process(self, project_id):
        self.calls.append(("get_active_process", project_id))
        return self.active

    async def list_processes(self, project_id):
        self.calls.append(("list_processes", project_id))
        return self.processes

    async def create_process(self, process):
        self.calls.append(("create_process", process))
        return {"id": "process-1"}


def test_returns_existing_active_process_without_creating_memory():
    async def scenario():
        memory = FakeMemory(active={"ID": "process-existing"})
        node = make_ensure_active_process(memory)

        result = await node({"root_session_id": "session-1", "project_id": "project-1"})

        assert result == {
            "project_id": "project-1",
            "active_process_id": "process-existing",
        }
        assert memory.calls == [("get_active_process", "project-1")]

    asyncio.run(scenario())


def test_creates_process_key_and_categories_when_no_active_process_exists():
    async def scenario():
        memory = FakeMemory(processes=[{"name": "process_001"}, {"name": "process_003"}])
        node = make_ensure_active_process(memory)

        result = await node({"root_session_id": "session-1"})

        assert result == {"project_id": "project-1", "active_process_id": "process-1"}
        assert memory.calls[:2] == [
            ("ensure_session_project", "session-1"),
            ("get_active_process", "project-1"),
        ]
        assert memory.calls[2] == (
            "list_processes",
            "project-1",
        )
        assert memory.calls[3][1] == {
            "project_id": "project-1",
            "name": "process_004",
        }

    asyncio.run(scenario())


def test_requires_root_session_when_project_is_not_in_state():
    async def scenario():
        memory = FakeMemory()
        node = make_ensure_active_process(memory)

        try:
            await node({})
        except ValueError as error:
            assert str(error) == "root_session_id is required to ensure an active process"
        else:
            raise AssertionError("expected missing root_session_id error")

    asyncio.run(scenario())
