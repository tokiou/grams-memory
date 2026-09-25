"""Strict, retry-aware process lifecycle operations backed by Memory MCP."""

from __future__ import annotations

import re
from typing import Any, Literal, Protocol, TypedDict

from supervisor.memory.client import MemoryClient, _field


TerminalProcessStatus = Literal["SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"]


class ProcessReference(TypedDict, total=False):
    id: str
    project_id: str
    key_id: str
    name: str
    status: str
    predecessor_id: str | None
    cycle_complete: bool
    recovered_transition: bool


class ProcessLifecycle(Protocol):
    async def ensure_active(self, project_id: str, cycle_id: str | None = None) -> ProcessReference: ...
    async def close_current(self, process_id: str, status: TerminalProcessStatus) -> ProcessReference: ...
    async def create_successor(
        self, project_id: str, predecessor_id: str, name: str, description: str = ""
    ) -> ProcessReference: ...
    async def next_process_name(self, project_id: str, predecessor_id: str | None = None) -> str: ...


class ProcessService:
    """Validate MCP process responses and reconcile repeated lifecycle calls."""

    _TERMINAL = {"SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"}
    _PROCESS_NAME = re.compile(r"^process_(\d+)(?:_|$)")
    _SUMMARY_OUTCOME = re.compile(
        r"^Process summary \[cycle-[0-9a-f]{20}\]: (SUCCEEDED|FAILED|SUPERSEDED|ABANDONED)$"
    )

    def __init__(self, memory: MemoryClient) -> None:
        self.memory = memory

    async def _cycle_summaries(self, cycle_id: str, project_id: str, key_id: str) -> list[dict[str, Any]]:
        # Intervention audit records share the cycle marker. They are not
        # process summaries and must never trigger lifecycle recovery.
        prefix = f"Process summary [{cycle_id}]: "
        values = await self.memory.search(
            cycle_id,
            project_id=project_id,
            key_id=key_id,
            limit=10,
        )
        return [item for item in values if str(_field(item, "title") or "").startswith(prefix)]

    async def ensure_active(self, project_id: str, cycle_id: str | None = None) -> ProcessReference:
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project_id is required to find an active process")
        process = await self.memory.get_active_process(project_id)
        if process is not None:
            active = self._validate_reference(process, project_id=project_id, expected_status="ACTIVE")
            if cycle_id:
                summaries = await self._cycle_summaries(cycle_id, project_id, active["key_id"])
                summary = next(iter(summaries), None)
                if summary is not None:
                    match = self._SUMMARY_OUTCOME.match(str(_field(summary, "title") or ""))
                    if match is None:
                        raise RuntimeError("persisted process summary has an invalid outcome marker")
                    outcome = match.group(1)
                    closed = await self.close_current(active["id"], outcome)
                    if outcome == "SUPERSEDED":
                        name = await self.next_process_name(project_id, active["id"])
                        successor = await self.create_successor(project_id, active["id"], name)
                        successor["recovered_transition"] = True
                        return successor
                    closed["cycle_complete"] = True
                    return closed
            predecessor_id = active.get("predecessor_id")
            if cycle_id and predecessor_id:
                predecessor = self._validate_reference(
                    await self.memory.get_process(predecessor_id),
                    expected_id=predecessor_id,
                    project_id=project_id,
                )
                summaries = await self._cycle_summaries(cycle_id, project_id, predecessor["key_id"])
                if summaries:
                    active["recovered_transition"] = True
            return active
        processes = await self.memory.list_processes(project_id)
        if processes:
            references = [self._validate_reference(existing, project_id=project_id) for existing in processes]
            latest = max(references, key=lambda item: self._process_number(item["name"]))
            if latest["status"] == "SUPERSEDED":
                name = await self.next_process_name(project_id, latest["id"])
                successor = await self.create_successor(project_id, latest["id"], name)
                successor["recovered_transition"] = True
                return successor
            if cycle_id:
                summaries = await self._cycle_summaries(cycle_id, project_id, latest["key_id"])
                if summaries:
                    latest["cycle_complete"] = True
                    return latest
            name = await self.next_process_name(project_id)
            created = await self.memory.create_process({
                "project_id": project_id,
                "name": name,
                "description": "Supervised execution process after terminal closure",
            })
            return self._validate_reference(
                created, project_id=project_id, expected_status="ACTIVE", expected_name=name
            )
        created = await self.memory.create_process({
            "project_id": project_id,
            "name": "process_001",
            "description": "Initial supervised execution process",
        })
        return self._validate_reference(
            created, project_id=project_id, expected_status="ACTIVE", expected_name="process_001"
        )

    async def close_current(self, process_id: str, status: TerminalProcessStatus) -> ProcessReference:
        if not isinstance(process_id, str) or not process_id.strip():
            raise ValueError("process_id is required to close a process")
        if status not in self._TERMINAL:
            raise ValueError(f"invalid terminal process status: {status}")
        current = await self.memory.get_process(process_id)
        reference = self._validate_reference(current, expected_id=process_id)
        if reference["status"] == status:
            return reference
        if reference["status"] != "ACTIVE":
            raise RuntimeError(
                f"process {process_id} is already closed as {reference['status']}, not {status}"
            )
        process = await self.memory.close_process(process_id, status)
        return self._validate_reference(
            process,
            expected_id=process_id,
            project_id=reference["project_id"],
            expected_status=status,
            expected_name=reference["name"],
        )

    async def next_process_name(self, project_id: str, predecessor_id: str | None = None) -> str:
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project_id is required to derive a process name")
        highest = 0
        for process in await self.memory.list_processes(project_id):
            reference = self._validate_reference(process, project_id=project_id)
            if (
                predecessor_id is not None
                and reference["status"] == "ACTIVE"
                and reference.get("predecessor_id") == predecessor_id
            ):
                return reference["name"]
            match = self._PROCESS_NAME.match(reference["name"])
            if match:
                highest = max(highest, int(match.group(1)))
        return f"process_{highest + 1:03d}"

    @classmethod
    def _process_number(cls, name: str) -> int:
        match = cls._PROCESS_NAME.match(name)
        return int(match.group(1)) if match else 0

    async def create_successor(
        self,
        project_id: str,
        predecessor_id: str,
        name: str,
        description: str = "",
    ) -> ProcessReference:
        if not all(isinstance(value, str) and value.strip() for value in (project_id, predecessor_id, name)):
            raise ValueError("project_id, predecessor_id, and name are required to create a successor")
        predecessor = self._validate_reference(
            await self.memory.get_process(predecessor_id),
            expected_id=predecessor_id,
            project_id=project_id,
        )
        if predecessor["status"] != "SUPERSEDED":
            raise RuntimeError("a successor requires a SUPERSEDED predecessor")

        for process in await self.memory.list_processes(project_id):
            reference = self._validate_reference(process, project_id=project_id)
            if reference["status"] == "ACTIVE":
                if reference.get("predecessor_id") == predecessor_id:
                    return reference
                raise RuntimeError("project already has a different active process")

        process = await self.memory.create_process({
            "project_id": project_id,
            "name": name.strip(),
            "description": description,
            "predecessor_id": predecessor_id,
        })
        reference = self._validate_reference(
            process,
            project_id=project_id,
            expected_status="ACTIVE",
            expected_name=name.strip(),
            expected_predecessor=predecessor_id,
        )
        if reference["id"] == predecessor_id:
            raise RuntimeError("Memory MCP returned the predecessor as its successor")
        return reference

    @staticmethod
    def _validate_reference(
        process: Any,
        *,
        expected_id: str | None = None,
        project_id: str | None = None,
        expected_status: str | None = None,
        expected_name: str | None = None,
        expected_predecessor: str | None = None,
    ) -> ProcessReference:
        if not isinstance(process, dict):
            raise RuntimeError("Memory MCP returned an invalid process")
        fields = {
            name: _field(process, name)
            for name in ("id", "project_id", "key_id", "name", "status", "predecessor_id")
        }
        for required in ("id", "project_id", "key_id", "name", "status"):
            if not isinstance(fields[required], str) or not fields[required].strip():
                raise RuntimeError(f"Memory MCP returned a process without a valid {required}")
        if fields["status"] not in {"ACTIVE", "SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"}:
            raise RuntimeError("Memory MCP returned a process with an invalid status")
        if fields["predecessor_id"] is not None and (
            not isinstance(fields["predecessor_id"], str) or not fields["predecessor_id"].strip()
        ):
            raise RuntimeError("Memory MCP returned a process with an invalid predecessor_id")
        reference: ProcessReference = {
            "id": fields["id"],
            "project_id": fields["project_id"],
            "key_id": fields["key_id"],
            "name": fields["name"],
            "status": fields["status"],
            "predecessor_id": fields["predecessor_id"] or None,
        }
        if expected_id is not None and reference["id"] != expected_id:
            raise RuntimeError("Memory MCP returned the wrong process id")
        if project_id is not None and reference["project_id"] != project_id:
            raise RuntimeError("Memory MCP returned a process for the wrong project")
        if expected_status is not None and reference["status"] != expected_status:
            raise RuntimeError(
                f"Memory MCP returned process with status {reference['status']}, expected {expected_status}"
            )
        if expected_name is not None and reference["name"] != expected_name:
            raise RuntimeError("Memory MCP returned a process with the wrong name")
        if expected_predecessor is not None and reference.get("predecessor_id") != expected_predecessor:
            raise RuntimeError("Memory MCP returned a process with the wrong predecessor")
        return reference
