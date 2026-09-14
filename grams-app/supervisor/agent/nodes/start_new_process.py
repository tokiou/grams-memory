"""New-process creation node stub."""

from supervisor.agent.state import SupervisorState


async def start_new_process(state: SupervisorState) -> dict:
    """Create the successor process after a real strategic pivot.

    Future implementation: use pending_process_transition, create the process
    through Memory MCP, and preserve a relation such as SUPERSEDES. It must
    not invent a pivot or use an LLM.
    """
    raise NotImplementedError
