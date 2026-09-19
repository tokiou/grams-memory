from __future__ import annotations


def make_finalize_cycle(inbox):
    async def node(state):
        claims = []
        for event in state.get("claimed_events") or []:
            event_id = event.get("id")
            lease_id = event.get("lease_id")
            if not event_id or not lease_id:
                raise ValueError("claimed events require id and lease_id")
            claims.append((event_id, lease_id))
        if not await inbox.ack_batch(claims):
            raise RuntimeError("Inbox batch ACK rejected one or more event leases")
        acknowledged = [event_id for event_id, _ in claims]
        return {
            "acknowledged_event_ids": acknowledged,
            "claimed_events": [],
            "proposed_memory_update": {"memories": [], "relations": []},
            "memory_update_result": {},
            "supervision_decision": {},
            "supervision_diagnostics": {},
            "process_continuity": {},
            "pending_process_transition": {},
            "pending_process_summary": {},
            "expanded_memory_context": {},
            "memory_expansion_depth": 0,
            "memory_expansion_exhausted": False,
            "intervention_message": "",
            "intervention_result": {},
            "context_reload_reason": "",
            "context_route": "",
            "cycle_already_completed": False,
            "final_status": "FINALIZED",
        }

    return node
