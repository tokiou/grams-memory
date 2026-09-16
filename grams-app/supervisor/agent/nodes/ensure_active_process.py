"""Ensure that a session has one active process in Memory MCP."""

from __future__ import annotations

import re
from typing import Any

from supervisor.agent.state import SupervisorState
from supervisor.memory.client import MemoryClient, _field


_PROCESS_NAME = re.compile(r"^process_(\d+)$")


def _process_number(processes: list[dict[str, Any]]) -> int:
    numbers = []
    for process in processes:
        match = _PROCESS_NAME.match(str(_field(process, "name") or ""))
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def make_ensure_active_process(memory: MemoryClient):
    """Build the deterministic process-initialization node."""

    async def ensure_active_process(state: SupervisorState) -> dict[str, str]:
        """Ensure and return the ACTIVE process identity for this session.

        The Project is the durable scope for ``root_session_id``. The node only
        resolves process identity or delegates atomic process creation to
        Memory MCP; it does not interpret events, assess strategy quality,
        detect pivots, or call an LLM.
        """
        root_session_id = state.get("root_session_id")
        if not root_session_id:
            raise ValueError("root_session_id is required to ensure an active process")

        project_id = state.get("project_id")
        if not project_id:
            project_id = await memory.ensure_session_project(root_session_id)

        active = await memory.get_active_process(project_id)
        if active is not None:
            process_id = _field(active, "id")
            if not process_id:
                raise RuntimeError("Memory MCP returned an active process without an id")
            return {"project_id": project_id, "active_process_id": str(process_id)}

        processes = await memory.list_processes(project_id)
        process_name = f"process_{_process_number(processes):03d}"
        try:
            process = await memory.create_process({
                "project_id": project_id,
                "name": process_name,
            })
        except Exception:
            # Another supervisor may have won the single-ACTIVE race while the
            # key and categories were being created. Re-read the source of truth
            # before surfacing the original failure.
            active = await memory.get_active_process(project_id)
            if active is None or not _field(active, "id"):
                raise
            return {"project_id": project_id, "active_process_id": str(_field(active, "id"))}

        process_id = _field(process, "id")
        if not process_id:
            raise RuntimeError("Memory MCP process_create returned a process without an id")
        return {"project_id": project_id, "active_process_id": str(process_id)}

    return ensure_active_process


async def ensure_active_process(state: SupervisorState) -> dict[str, str]:
    """Compatibility wrapper requiring an injected Memory MCP client.

    Graph construction should use ``make_ensure_active_process`` so the client
    dependency remains explicit and testable.
    """
    raise RuntimeError("ensure_active_process requires a Memory MCP client; use make_ensure_active_process")
