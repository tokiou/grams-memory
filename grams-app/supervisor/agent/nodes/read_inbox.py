from __future__ import annotations

def make_read_inbox(inbox, *, batch_size=20, run_id=None):
    async def node(state):
        if "claimed_events" in state:
            return {"claimed_events": state["claimed_events"]}
        events = await inbox.claim_pending(state["root_session_id"], batch_size, run_id=run_id)
        return {"claimed_events": [{"id": e.id, "lease_id": e.lease_id, "root_session_id": e.root_session_id, "session_id": e.session_id, "type": e.type, "source_event": e.source_event, "payload": e.payload, "received_at": e.received_at.isoformat(), "source_run_id": e.source_run_id, "sequence": e.sequence, "ingress_id": e.ingress_id, "cycle_id": getattr(e, "cycle_id", None)} for e in events]}
    return node
