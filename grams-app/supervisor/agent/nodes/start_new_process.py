from __future__ import annotations

from supervisor.agent.services.process_service import ProcessLifecycle
from supervisor.agent.state import SupervisorState


def make_start_new_process(process_service: ProcessLifecycle):
    async def node(state: SupervisorState):
        project_id = state.get("project_id")
        predecessor_id = state.get("pending_process_transition", {}).get("predecessor_id")
        if not project_id or predecessor_id != state.get("active_process_id"):
            raise ValueError("a valid pending process transition is required")
        name = await process_service.next_process_name(project_id, predecessor_id)
        process = await process_service.create_successor(project_id, predecessor_id, name)
        return {
            "active_process_id": process["id"],
            "final_status": "NEW_PROCESS",
            "context_reload_reason": "new_process",
            "pending_process_transition": {},
            "pending_process_summary": {},
            "process_continuity": {},
            "expanded_memory_context": {},
            "memory_expansion_depth": 0,
            "memory_expansion_exhausted": False,
        }

    return node
