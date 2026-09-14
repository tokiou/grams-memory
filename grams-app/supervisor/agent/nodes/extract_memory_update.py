"""Memory-update extraction node stub."""

from supervisor.agent.state import SupervisorState


async def extract_memory_update(state: SupervisorState) -> dict:
    """Propose new STRATEGY, EVIDENCE, and relation data from recent events.

    Future implementation: compare events with process context to avoid
    duplicates. It must not create SUMMARY, decide continuation, intervene, or
    write to Memory MCP. It uses a dedicated LLM prompt.
    """
    raise NotImplementedError
