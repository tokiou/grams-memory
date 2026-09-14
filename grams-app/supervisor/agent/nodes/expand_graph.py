"""Graph-expansion node stub."""

from supervisor.agent.state import SupervisorState


async def expand_graph(state: SupervisorState) -> dict:
    """Load additional graph context requested by REVIEW.

    Future implementation: retrieve requested memories, relations, neighbors,
    summaries, or related processes through Memory MCP and increment expansion
    depth. It must not use an LLM.
    """
    raise NotImplementedError
