import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.agent.nodes.ensure_active_process import make_ensure_active_process
from supervisor.agent.services.process_service import ProcessService


def process(process_id="process-existing", status="ACTIVE", name="process_001"):
    return {
        "ID": process_id,
        "ProjectID": "project-1",
        "KeyID": f"key-{process_id}",
        "Name": name,
        "Status": status,
    }


class FakeMemory:
    def __init__(self, *, active=None, processes=None):
        self.active = active
        self.processes = list(processes or [])
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

    async def create_process(self, value):
        self.calls.append(("create_process", value))
        return process(name=value["name"])


def test_returns_existing_strict_active_process_without_creating_memory():
    async def scenario():
        memory = FakeMemory(active=process())
        node = make_ensure_active_process(ProcessService(memory))
        result = await node({"root_session_id": "session-1", "project_id": "project-1"})
        assert result == {"project_id": "project-1", "active_process_id": "process-existing"}
        assert memory.calls == [("get_active_process", "project-1")]

    asyncio.run(scenario())


def test_creates_first_process_only_for_an_empty_project():
    async def scenario():
        memory = FakeMemory()
        node = make_ensure_active_process(ProcessService(memory))
        result = await node({"root_session_id": "session-1", "project_id": "project-1"})
        assert result["active_process_id"] == "process-existing"
        assert memory.calls[-1] == ("create_process", {
            "project_id": "project-1",
            "name": "process_001",
            "description": "Initial supervised execution process",
        })

    asyncio.run(scenario())


def test_requires_root_session_when_project_is_not_in_state():
    async def scenario():
        node = make_ensure_active_process(ProcessService(FakeMemory()))
        try:
            await node({})
        except ValueError as error:
            assert str(error) == "root_session_id is required to ensure an active process"
        else:
            raise AssertionError("expected missing root_session_id error")

    asyncio.run(scenario())
