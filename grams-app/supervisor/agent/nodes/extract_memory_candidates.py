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
    visible_events = [event for event in payload.get("recent_execution") or [] if event.get("id")]
    payload["claimed_event_ids"] = [str(event["id"]) for event in visible_events]
    payload["claimed_event_types"] = {
        str(event["id"]): str(event.get("type") or "")
        for event in visible_events
    }
    payload["cycle_id"] = cycle_key(state) if state.get("claimed_events") else None
    return payload


def make_extract_memory_candidates(openrouter):
    async def node(state):
        value = await openrouter.generate_json(
            operation="EXTRACT_MEMORY_CANDIDATES",
            payload=_candidate_payload(state),
            system_prompt=MEMORY_CANDIDATE_SYSTEM_PROMPT,
            schema=MEMORY_CANDIDATES_JSON_SCHEMA,
        )
        visible_events = [
            event
            for event in build_jev_process_state(state).get("recent_execution") or []
            if event.get("id")
        ]
        expected_cycle_id = cycle_key(state) if state.get("claimed_events") else None
        return {
            "memory_candidates": validate_memory_candidates(
                value,
                visible_events,
                expected_cycle_id=expected_cycle_id,
            ),
        }

    return node
