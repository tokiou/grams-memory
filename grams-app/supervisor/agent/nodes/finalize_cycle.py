"""Cycle-finalization node stub."""

from supervisor.agent.state import SupervisorState


async def finalize_cycle(state: SupervisorState) -> dict:
    """Finish the current batch without implying that the task is complete.

    Future implementation: acknowledge claimed event IDs through Inbox,
    persist required operational state, and clear transient cycle fields. END
    means only that this batch finished. It must not use an LLM.
    """
    raise NotImplementedError
