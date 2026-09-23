from __future__ import annotations

from supervisor.agent.prompts import INTERVENTION_SYSTEM_PROMPT
from supervisor.agent.schemas import REASON_CODES
from supervisor.agent.services.openrouter_service import OpenRouterClient
from supervisor.agent.state import SupervisorState
from supervisor.agent.state_builder import build_jev_process_state


def make_build_intervention(openrouter: OpenRouterClient):
    async def node(state: SupervisorState):
        decision = state.get("supervision_decision") or {}
        if decision.get("action") != "INTERVENE":
            raise ValueError("BUILD_INTERVENTION requires an INTERVENE decision")
        evidence_ids = decision.get("evidence_memory_ids")
        reason_codes = decision.get("reason_codes")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            raise ValueError("INTERVENE requires evidence_memory_ids")
        if not isinstance(reason_codes, list) or not reason_codes:
            raise ValueError("INTERVENE requires reason_codes")
        if any(reason not in REASON_CODES for reason in reason_codes):
            raise ValueError("INTERVENE contains an unsupported reason code")
        canonical_state = build_jev_process_state(state)
        available = {
            str(memory.get("id")): memory
            for memory in canonical_state.get("evidence") or []
            if memory.get("id")
        }
        evidence_category_id = (state.get("process_context") or {}).get("category_ids", {}).get("EVIDENCE")
        if evidence_category_id is not None:
            for memory in ((canonical_state.get("expanded_memory") or {}).get("memories") or {}).values():
                memory_id = memory.get("id")
                if memory_id and str(memory.get("category_id")) == str(evidence_category_id):
                    available.setdefault(str(memory_id), memory)
        if len(set(evidence_ids)) != len(evidence_ids) or any(memory_id not in available for memory_id in evidence_ids):
            raise ValueError("evidence_memory_ids are outside the current EVIDENCE scope")
        selected_memories = [available[memory_id] for memory_id in evidence_ids]
        intervention_state = {
            **canonical_state,
            # The full process context remains available through explicit fields;
            # evidence is restricted to the memories JEV selected.
            "strategy": [],
            "evidence": selected_memories,
            "relations": [],
            "expanded_memory": {},
        }
        message = await openrouter.generate_text(
            operation="BUILD_INTERVENTION",
            payload={
                "state": intervention_state,
                "decision": decision,
                "reason_codes": reason_codes,
                "evidence_memory_ids": evidence_ids,
                "selected_memories": selected_memories,
                "summary": canonical_state.get("summary"),
                "diagnostics": state.get("supervision_diagnostics") or {},
            },
            system_prompt=INTERVENTION_SYSTEM_PROMPT,
        )
        if not isinstance(message, str) or not message.strip():
            raise ValueError("OpenRouter returned an empty intervention")
        return {"intervention_message": message.strip()}

    return node
