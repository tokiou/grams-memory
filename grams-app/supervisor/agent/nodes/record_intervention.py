"""Intervention-recording node stub."""

from supervisor.agent.state import SupervisorState


async def record_intervention(state: SupervisorState) -> dict:
    """Persist an intervention audit record for the active process.

    Future implementation: record the reason, evidence, guidance, and delivery
    result through Memory MCP. It must not use an LLM.
    """
    raise NotImplementedError
