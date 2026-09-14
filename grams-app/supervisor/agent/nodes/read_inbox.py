"""Inbox ingestion node stub."""

from supervisor.agent.state import SupervisorState


async def read_inbox(state: SupervisorState) -> dict:
    """Claim a batch from the Inbox and expose only the current cycle delta.

    Future implementation: read through EventInbox, store the event payloads as
    `recent_events`, and retain IDs plus leases for final acknowledgement.
    It must not interpret events, call an LLM, consult memory, detect progress,
    or decide interventions.
    """
    raise NotImplementedError
