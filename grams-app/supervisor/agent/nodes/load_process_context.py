"""Process-context loading node stub."""

from supervisor.agent.state import SupervisorState


async def load_process_context(state: SupervisorState) -> dict:
    """Load a compact, current view of the ACTIVE process graph.

    Future implementation: read process identity, status, STRATEGY, EVIDENCE,
    SUMMARY, relevant relations, and recent changes from Memory MCP. This node
    is reused before continuity assessment, after memory updates, and after a
    new process starts. It must not use an LLM.
    """
    raise NotImplementedError
