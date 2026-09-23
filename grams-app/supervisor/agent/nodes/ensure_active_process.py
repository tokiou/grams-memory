from __future__ import annotations

from supervisor.agent.nodes.common import cycle_key
from supervisor.agent.services.process_service import ProcessLifecycle
from supervisor.agent.state import SupervisorState


def make_ensure_active_process(process_service: ProcessLifecycle):
    async def node(state: SupervisorState):
        if not state.get("root_session_id"):
            raise ValueError("root_session_id is required to ensure an active process")
        project_id = state.get("project_id") or await process_service.memory.ensure_session_project(state["root_session_id"])
        process = await process_service.ensure_active(
            project_id,
            cycle_key(state) if state.get("claimed_events") else None,
        )
        update = {
            "project_id": project_id,
            "active_process_id": process["id"],
        }
        if process.get("cycle_complete"):
            update["cycle_already_completed"] = True
        if process.get("recovered_transition"):
            update["context_reload_reason"] = "new_process"
        return update
    return node
