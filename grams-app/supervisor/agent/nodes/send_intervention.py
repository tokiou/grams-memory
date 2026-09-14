"""Intervention-delivery node stub."""

from supervisor.agent.state import SupervisorState


async def send_intervention(state: SupervisorState) -> dict:
    """Deliver an intervention already selected by REVIEW to OpenCode.

    Future implementation: use the review reason, evidence IDs, and guidance
    through the retained OpenCode client. It must not decide again whether to
    intervene or solve the task.
    """
    raise NotImplementedError
