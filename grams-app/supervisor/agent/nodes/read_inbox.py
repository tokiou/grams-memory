"""Inbox ingestion node."""

from __future__ import annotations

from typing import Any

from supervisor.inbox import EventInbox
from supervisor.agent.state import SupervisorState


def make_read_inbox_node(
    inbox: EventInbox,
    *,
    batch_size: int = 20,
    run_id: str | None = None,
):
    """Build a node that claims one durable Inbox batch per cycle."""

    async def read_inbox(state: SupervisorState) -> dict[str, Any]:
        """Claim and expose the current cycle's events without interpreting them.

        Events are claimed from the durable Inbox using the session scope. The
        returned envelopes retain both `id` and `lease_id` so a later cycle
        finalizer can ACK or fail exactly the claims made here. The payload is
        copied as-is into a serializable state value. This node does not ACK,
        interpret events, call an LLM, consult memory, detect progress, or make
        intervention decisions.
        """
        root_session_id = state.get("root_session_id")
        if not root_session_id:
            raise ValueError("root_session_id is required to read the Inbox")

        events = await inbox.claim_pending(
            root_session_id,
            limit=batch_size,
            run_id=run_id,
        )
        claimed_events = [
            {
                "id": event.id,
                "lease_id": event.lease_id or "",
                "root_session_id": event.root_session_id,
                "session_id": event.session_id,
                "type": event.type,
                "source_event": event.source_event,
                "payload": event.payload,
                "received_at": event.received_at.isoformat(),
                "source_run_id": event.source_run_id,
                "sequence": event.sequence,
                "ingress_id": event.ingress_id,
            }
            for event in events
        ]
        return {"claimed_events": claimed_events}

    return read_inbox
