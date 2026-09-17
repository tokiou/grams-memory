import asyncio

from supervisor.agent.services.process_service import ProcessService


class FakeMemory:
    def __init__(self):
        self.calls = []

    async def get_active_process(self, project_id):
        self.calls.append(("get_active_process", project_id))
        return {"id": "process-1", "project_id": project_id, "status": "ACTIVE"}

    async def close_process(self, process_id, status):
        self.calls.append(("close_process", process_id, status))
        return {"id": process_id, "status": status}

    async def create_process(self, process):
        self.calls.append(("create_process", process))
        return {"id": "process-2", "project_id": process["project_id"], "status": "ACTIVE"}


def test_process_service_delegates_lifecycle_operations():
    async def scenario():
        memory = FakeMemory()
        service = ProcessService(memory)

        assert (await service.ensure_active("project-1"))["id"] == "process-1"
        assert (await service.close_current("process-1", "SUPERSEDED"))["status"] == "SUPERSEDED"
        assert (await service.create_successor("project-1", "process-1", "process_002"))["id"] == "process-2"
        assert memory.calls == [
            ("get_active_process", "project-1"),
            ("close_process", "process-1", "SUPERSEDED"),
            ("create_process", {
                "project_id": "project-1",
                "name": "process_002",
                "description": "",
                "predecessor_id": "process-1",
            }),
        ]

    asyncio.run(scenario())


def test_process_service_rejects_missing_active_process_and_invalid_status():
    async def scenario():
        memory = FakeMemory()
        memory.get_active_process = lambda project_id: _none()
        service = ProcessService(memory)

        try:
            await service.ensure_active("project-1")
        except RuntimeError as error:
            assert str(error) == "No active process found for project project-1"
        else:
            raise AssertionError("expected missing active process error")

        try:
            await service.close_current("process-1", "ACTIVE")
        except ValueError as error:
            assert str(error) == "invalid terminal process status: ACTIVE"
        else:
            raise AssertionError("expected invalid status error")

    asyncio.run(scenario())


async def _none():
    return None
