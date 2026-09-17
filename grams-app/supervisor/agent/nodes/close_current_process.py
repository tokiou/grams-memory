"""Close the current process through the process lifecycle service."""

from supervisor.agent.state import SupervisorState
from typing import cast

from supervisor.agent.services.process_service import ProcessService, TerminalProcessStatus


def make_close_current_process(processes: ProcessService):
    async def close_current_process(state: SupervisorState) -> dict:
        process_id = state.get("active_process_id")
        if not process_id:
            raise ValueError("active_process_id is required to close a process")
        transition = state.get("pending_process_transition")
        outcome = "SUPERSEDED" if transition else state.get("review_decision", {}).get("process_outcome")
        if outcome not in ProcessService._TERMINAL:
            raise ValueError("a terminal process outcome is required to close a process")
        closed = await processes.close_current(process_id, cast(TerminalProcessStatus, outcome))
        return {"closed_process_id": closed["id"]}

    return close_current_process


async def close_current_process(state: SupervisorState) -> dict:
    """Persist the pending summary and close the ACTIVE process.

    Future implementation: apply SUCCEEDED, FAILED, SUPERSEDED, or ABANDONED
    through Memory MCP. A pending process transition normally closes the old
    process as SUPERSEDED. It must not use an LLM.
    """
    raise RuntimeError("close_current_process requires a ProcessService; use make_close_current_process")
