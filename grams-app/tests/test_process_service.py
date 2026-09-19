import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.agent.services.process_service import ProcessService


def process(process_id, status, name, predecessor_id=None):
    return {
        "id": process_id,
        "project_id": "project-1",
        "key_id": f"key-{process_id}",
        "name": name,
        "status": status,
        "predecessor_id": predecessor_id,
    }


class FakeMemory:
    def __init__(self):
        self.calls = []
        self.items = {"process-1": process("process-1", "ACTIVE", "process_001")}

    async def get_active_process(self, project_id):
        self.calls.append(("get_active_process", project_id))
        return next((item for item in self.items.values() if item.get("status") == "ACTIVE"), None)

    async def get_process(self, process_id):
        self.calls.append(("get_process", process_id))
        return self.items.get(process_id)

    async def list_processes(self, project_id):
        self.calls.append(("list_processes", project_id))
        return list(self.items.values())

    async def close_process(self, process_id, status):
        self.calls.append(("close_process", process_id, status))
        self.items[process_id] = {**self.items[process_id], "status": status}
        return self.items[process_id]

    async def create_process(self, value):
        self.calls.append(("create_process", value))
        created = process("process-2", "ACTIVE", value["name"], value.get("predecessor_id"))
        self.items["process-2"] = created
        return created


def test_process_service_delegates_and_reconciles_lifecycle_operations():
    async def scenario():
        memory = FakeMemory()
        service = ProcessService(memory)
        assert (await service.ensure_active("project-1"))["id"] == "process-1"
        assert (await service.close_current("process-1", "SUPERSEDED"))["status"] == "SUPERSEDED"
        assert await service.next_process_name("project-1") == "process_002"
        successor = await service.create_successor("project-1", "process-1", "process_002")
        assert successor["id"] == "process-2"
        assert await service.next_process_name("project-1", "process-1") == "process_002"
        assert (await service.create_successor("project-1", "process-1", "process_002"))["id"] == "process-2"
        assert len([call for call in memory.calls if call[0] == "create_process"]) == 1
        assert len([call for call in memory.calls if call[0] == "close_process"]) == 1

    asyncio.run(scenario())


def test_process_service_rejects_invalid_status_and_incomplete_mcp_response():
    async def scenario():
        memory = FakeMemory()
        service = ProcessService(memory)
        try:
            await service.close_current("process-1", "ACTIVE")
        except ValueError as error:
            assert str(error) == "invalid terminal process status: ACTIVE"
        else:
            raise AssertionError("expected invalid status error")
        async def incomplete(_project_id):
            return {"id": "process-1"}

        memory.get_active_process = incomplete
        try:
            await service.ensure_active("project-1")
        except RuntimeError as error:
            assert "valid project_id" in str(error)
        else:
            raise AssertionError("expected strict process validation error")

    asyncio.run(scenario())
