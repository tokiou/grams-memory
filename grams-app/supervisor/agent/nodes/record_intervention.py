from __future__ import annotations

import json

from supervisor.agent.state import SupervisorState
from supervisor.memory.client import MemoryClient, _field


def _audit_description(state: SupervisorState) -> str:
    decision = state.get("supervision_decision") or {}
    value = {
        "version": 1,
        "action": decision.get("action"),
        "action_confidence": decision.get("action_confidence"),
        "evidence_memory_ids": decision.get("evidence_memory_ids") or [],
        "reason_codes": decision.get("reason_codes") or [],
    }
    return "GRAMS_INTERVENTION_V1:" + json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)

def make_record_intervention(memory: MemoryClient):
    async def node(state: SupervisorState):
        category_id = state.get("process_context", {}).get("key", {}).get("evidence_category_id")
        message = state.get("intervention_result", {}).get("message")
        if not category_id:
            raise ValueError("evidence_category_id is required to record an intervention")
        if not isinstance(message, str) or not message:
            raise ValueError("delivered intervention message is required for audit")
        intervention = state.get("intervention_result", {})
        if intervention.get("delivered") is not True and intervention.get("delivery_status") not in {
            "PENDING", "CLAIMED",
        }:
            raise RuntimeError("cannot record an intervention that was not delivered or queued")
        if intervention.get("audit_recorded") and intervention.get("audit_memory_id"):
            return {"intervention_result": intervention}
        result = await memory.create({
            "category_id": category_id,
            "title": "Supervisor intervention",
            "content": message,
            "description": _audit_description(state),
            "source": "supervisor",
        })
        memory_id = _field(result, "id")
        if not memory_id or str(_field(result, "category_id")) != str(category_id):
            raise RuntimeError("Memory MCP returned an invalid intervention evidence record")
        return {"intervention_result": {
            **intervention,
            "audit_recorded": True,
            "audit_memory_id": str(memory_id),
        }}
    return node
