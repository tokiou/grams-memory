"""Create a successor process through the process lifecycle service."""

from supervisor.agent.state import SupervisorState
from supervisor.agent.services.process_service import ProcessService


def make_start_new_process(processes: ProcessService):
    async def start_new_process(state: SupervisorState) -> dict:
        project_id = state.get("project_id")
        predecessor_id = state.get("active_process_id")
        transition = state.get("pending_process_transition")
        if not project_id or not predecessor_id:
            raise ValueError("project_id and active_process_id are required to start a process")
        if not transition:
            raise ValueError("pending_process_transition is required to start a successor")
        name = transition.get("name") or transition.get("suggested_process_name")
        if not name:
            raise ValueError("pending process transition requires a name")
        successor = await processes.create_successor(
            project_id,
            predecessor_id,
            name,
            transition.get("description", ""),
        )
        return {"active_process_id": successor["id"]}

    return start_new_process


async def start_new_process(state: SupervisorState) -> dict:
    """Create the successor process after a real strategic pivot.

    Future implementation: use pending_process_transition, create the process
    through Memory MCP, and preserve a relation such as SUPERSEDES. It must
    not invent a pivot or use an LLM.
    """
    raise RuntimeError("start_new_process requires a ProcessService; use make_start_new_process")
