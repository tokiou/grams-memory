"""Process lifecycle operations backed by Memory MCP."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict

from supervisor.memory.client import MemoryClient, _field


TerminalProcessStatus = Literal["SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"]


class ProcessReference(TypedDict, total=False):
    id: str
    project_id: str
    status: str
    predecessor_id: str | None


class ProcessLifecycle(Protocol):
    async def ensure_active(self, project_id: str) -> ProcessReference: ...
    async def close_current(self, process_id: str, status: TerminalProcessStatus) -> ProcessReference: ...
    async def create_successor(self, project_id: str, predecessor_id: str, name: str, description: str = "") -> ProcessReference: ...


class ProcessService:
    """Validate and delegate process lifecycle mutations to Memory MCP."""

    _TERMINAL = {"SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"}

    def __init__(self, memory: MemoryClient) -> None:
        self.memory = memory

    async def ensure_active(self, project_id: str) -> ProcessReference:
        if not project_id:
            raise ValueError("project_id is required to find an active process")
        process = await self.memory.get_active_process(project_id)
        if process is None:
            raise RuntimeError(f"No active process found for project {project_id}")
        return self._validate_reference(process, project_id=project_id, expected_status="ACTIVE")

    async def close_current(self, process_id: str, status: TerminalProcessStatus) -> ProcessReference:
        if not process_id:
            raise ValueError("process_id is required to close a process")
        if status not in self._TERMINAL:
            raise ValueError(f"invalid terminal process status: {status}")
        process = await self.memory.close_process(process_id, status)
        reference = self._validate_reference(process, expected_status=status)
        return reference

    async def create_successor(
        self,
        project_id: str,
        predecessor_id: str,
        name: str,
        description: str = "",
    ) -> ProcessReference:
        if not project_id or not predecessor_id:
            raise ValueError("project_id and predecessor_id are required to create a successor")
        if not name or not name.strip():
            raise ValueError("name is required to create a successor")
        process = await self.memory.create_process({
            "project_id": project_id,
            "name": name,
            "description": description,
            "predecessor_id": predecessor_id,
        })
        reference = self._validate_reference(process, project_id=project_id, expected_status="ACTIVE")
        if reference["id"] == predecessor_id:
            raise RuntimeError("Memory MCP returned the predecessor as its successor")
        return reference

    def _validate_reference(
        self,
        process: dict[str, Any],
        *,
        project_id: str | None = None,
        expected_status: str | None = None,
    ) -> ProcessReference:
        process_id = _field(process, "id")
        if not process_id:
            raise RuntimeError("Memory MCP returned a process without an id")
        actual_project = _field(process, "project_id")
        if project_id is not None and actual_project is not None and str(actual_project) != project_id:
            raise RuntimeError("Memory MCP returned a process for the wrong project")
        actual_status = _field(process, "status")
        if expected_status and actual_status is not None and str(actual_status) != expected_status:
            raise RuntimeError(f"Memory MCP returned process with status {actual_status}, expected {expected_status}")
        return {
            "id": str(process_id),
            "project_id": str(actual_project or project_id or ""),
            "status": str(actual_status or expected_status or ""),
            "predecessor_id": _field(process, "predecessor_id"),
        }
