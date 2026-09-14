"""Process-closing node stub."""

from supervisor.agent.state import SupervisorState


async def close_current_process(state: SupervisorState) -> dict:
    """Persist the pending summary and close the ACTIVE process.

    Future implementation: apply SUCCEEDED, FAILED, SUPERSEDED, or ABANDONED
    through Memory MCP. A pending process transition normally closes the old
    process as SUPERSEDED. It must not use an LLM.
    """
    raise NotImplementedError
