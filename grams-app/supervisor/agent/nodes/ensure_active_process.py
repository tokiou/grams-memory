"""Ensure that a session has one active process in Memory MCP."""

from __future__ import annotations

from supervisor.agent.state import SupervisorState
from supervisor.agent.services.process_service import ProcessService


def make_ensure_active_process(processes: ProcessService):
    """Build the deterministic active-process lookup node."""

    async def ensure_active_process(state: SupervisorState) -> dict[str, str]:
        """Ensure and return the ACTIVE process identity for this session.

        The Project is the durable scope for ``root_session_id``. The node only
        resolves the current process identity; process creation belongs to the
        process-lifecycle flow.
        """
        if not state.get("root_session_id"):
            raise ValueError("root_session_id is required to ensure an active process")
        project_id = state.get("project_id")
        if not project_id:
            raise ValueError("project_id is required to ensure an active process")
        active = await processes.ensure_active(project_id)
        return {"project_id": project_id, "active_process_id": active["id"]}

    return ensure_active_process


async def ensure_active_process(state: SupervisorState) -> dict[str, str]:
    """Compatibility wrapper requiring an injected Memory MCP client.

    Graph construction should use ``make_ensure_active_process`` so the client
    dependency remains explicit and testable.
    """
    raise RuntimeError("ensure_active_process requires a Memory MCP client; use make_ensure_active_process")
