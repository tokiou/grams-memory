from __future__ import annotations

from supervisor.agent.state import SupervisorState
from supervisor.memory.client import MemoryClient


def make_record_intervention(memory: MemoryClient):
    # SEND_INTERVENTION persists and reconciles the audit by durable cycle key.
    # RECORD_INTERVENTION must not perform a second, non-atomic MCP create.
    async def node(state: SupervisorState):
        message = state.get("intervention_result", {}).get("message")
        if not isinstance(message, str) or not message:
            raise ValueError("delivered intervention message is required for audit")
        intervention = state.get("intervention_result", {})
        if intervention.get("delivered") is not True and intervention.get("delivery_status") not in {
            "PENDING", "CLAIMED",
        }:
            raise RuntimeError("cannot record an intervention that was not delivered or queued")
        if intervention.get("audit_recorded") is not True or not intervention.get("audit_memory_id"):
            raise RuntimeError("SEND_INTERVENTION must persist the intervention audit before recording")
        return {"intervention_result": intervention}

    return node
