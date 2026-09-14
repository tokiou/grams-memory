"""Memory-update application node stub."""

from supervisor.agent.state import SupervisorState


async def apply_memory_update(state: SupervisorState) -> dict:
    """Validate and apply the proposed memory update to Memory MCP.

    Future implementation: enforce categories, scope, process IDs, relation
    types, and obvious duplicate checks. This is the deterministic boundary
    between an LLM proposal and graph mutation; it does not use an LLM.
    """
    raise NotImplementedError
