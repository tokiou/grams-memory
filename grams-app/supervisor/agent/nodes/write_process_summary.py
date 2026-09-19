from __future__ import annotations
from supervisor.agent.prompts import PROCESS_SUMMARY_SYSTEM_PROMPT
from supervisor.agent.schemas import PROCESS_SUMMARY_JSON_SCHEMA
from supervisor.agent.schemas import validate_summary
from supervisor.agent.state_builder import build_jev_process_state

def make_write_process_summary(openrouter):
    async def node(state):
        pivot = state.get("process_continuity", {}).get("decision") == "NEW_PROCESS"
        outcome = "SUPERSEDED" if pivot else state.get("supervision_decision", {}).get("process_outcome")
        if outcome not in {"SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"}:
            raise ValueError("a deterministic process outcome is required before summary generation")
        value = await openrouter.generate_json(
            operation="WRITE_PROCESS_SUMMARY",
            payload={"state": build_jev_process_state(state), "outcome": outcome},
            system_prompt=PROCESS_SUMMARY_SYSTEM_PROMPT,
            schema=PROCESS_SUMMARY_JSON_SCHEMA,
        )
        return {"pending_process_summary": validate_summary({**value, "outcome": outcome})}
    return node
