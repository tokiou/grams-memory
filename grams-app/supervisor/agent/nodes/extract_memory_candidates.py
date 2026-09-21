from __future__ import annotations

from supervisor.agent.nodes.common import cycle_key
from supervisor.agent.prompts import MEMORY_CANDIDATE_SYSTEM_PROMPT
from supervisor.agent.schemas import (
    MEMORY_CANDIDATES_JSON_SCHEMA,
    validate_memory_candidates,
)
from supervisor.agent.state_builder import build_jev_process_state


def _candidate_payload(state):
    payload = build_jev_process_state(state)
    claimed_events = [event for event in state.get("claimed_events") or [] if isinstance(event, dict)]
    payload["claimed_event_ids"] = [str(event["id"]) for event in claimed_events if event.get("id")]
    payload["claimed_event_types"] = {
        str(event["id"]): str(event.get("type") or "")
        for event in claimed_events
        if event.get("id")
    }
    payload["cycle_id"] = cycle_key(state) if claimed_events else None
    return payload


def make_extract_memory_candidates(openrouter):
    async def node(state):
        value = await openrouter.generate_json(
            operation="EXTRACT_MEMORY_CANDIDATES",
            payload=_candidate_payload(state),
            system_prompt=MEMORY_CANDIDATE_SYSTEM_PROMPT,
            schema=MEMORY_CANDIDATES_JSON_SCHEMA,
        )
        return {
            "memory_candidates": validate_memory_candidates(
                value,
                [event for event in state.get("claimed_events") or [] if isinstance(event, dict)],
            ),
        }

    return node
