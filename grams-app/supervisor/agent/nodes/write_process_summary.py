"""Process-summary generation node stub."""

from supervisor.agent.state import SupervisorState


async def write_process_summary(state: SupervisorState) -> dict:
    """Generate a compact decision-relevant summary for the closing process.

    Future implementation: use an LLM to preserve strategy, decisive evidence,
    outcome, cause, and reusable knowledge while excluding trivial activity and
    unsupported claims. It must not write directly to Memory MCP.
    """
    raise NotImplementedError
