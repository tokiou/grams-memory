"""Active-process initialization node stub."""

from supervisor.agent.state import SupervisorState


async def ensure_active_process(state: SupervisorState) -> dict:
    """Ensure that the session has an ACTIVE process and return its ID.

    Future implementation: inspect or create the process through Memory MCP.
    It must not detect pivots, assess strategy quality, or use an LLM.
    """
    raise NotImplementedError
