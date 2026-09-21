from __future__ import annotations

from typing import Any


def make_assess_agent_health():
    async def node(state: dict[str, Any]) -> dict[str, Any]:
        events = state.get("claimed_events") or []
        errors = [event for event in events if event.get("type") == "SESSION_INTERNAL_ERROR"]
        active_error = next(
            (
                event
                for event in errors
                if not (event.get("payload") or {}).get("active_tool_call_id")
            ),
            None,
        )
        if active_error:
            payload = active_error.get("payload") or {}
            return {
                "health_route": "recover",
                "supervision_decision": {"action": "INTERVENE"},
                "intervention_message": (
                    "OpenCode encountered an internal session error. Restart the task from the beginning, "
                    "verify the current environment, and do not assume the previous execution completed. "
                    f"Error: {str(payload.get('message') or 'unknown internal error')[:500]}"
                ),
            }
        if events and all(event.get("type") == "SESSION_HEARTBEAT" for event in events):
            return {"health_route": "heartbeat"}
        return {"health_route": "normal"}

    return node
