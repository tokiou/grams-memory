from __future__ import annotations

from supervisor.memory.client import _field

def make_record_intervention(memory):
    async def node(state):
        category_id = state.get("process_context", {}).get("key", {}).get("evidence_category_id")
        message = state.get("intervention_result", {}).get("message")
        if not category_id:
            raise ValueError("evidence_category_id is required to record an intervention")
        if not isinstance(message, str) or not message:
            raise ValueError("delivered intervention message is required for audit")
        intervention = state.get("intervention_result", {})
        if intervention.get("audit_recorded") and intervention.get("audit_memory_id"):
            return {"intervention_result": intervention}
        result = await memory.create({
            "category_id": category_id,
            "title": "Supervisor intervention",
            "content": message,
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
