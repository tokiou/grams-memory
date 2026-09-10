"""The only node that incorporates new events into operational state."""

from datetime import datetime, timezone
import time
from typing import Any
import logging

from ..state import SupervisorState
from ...observability import emit

logger = logging.getLogger(__name__)

PROGRESS_EVENTS = {
    "TOOL_RESULT_FINAL", "FILE_CHANGE_FINAL", "MESSAGE_COMPLETED", "MESSAGE_ERROR",
}


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _event_dict(event: Any) -> dict[str, Any]:
    return {
        "id": event.id,
        "session_id": event.session_id,
        "root_session_id": event.root_session_id,
        "type": event.type,
        "source_event": event.source_event,
        "payload": event.payload,
        "received_at": event.received_at.isoformat(),
        "processing_at": event.processing_at.isoformat() if event.processing_at else None,
        "lease_id": event.lease_id,
    }


def _event_text(event: dict[str, Any]) -> str | None:
    payload = event.get("payload")
    for _ in range(2):
        if not isinstance(payload, dict):
            return None
        properties = payload.get("properties")
        if isinstance(properties, dict):
            part = properties.get("part")
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return part["text"]
        if isinstance(payload.get("text"), str):
            return payload["text"]
        payload = payload.get("payload")
    return None


def make_read_inbox_node(*, limit: int):
    async def read_inbox(state: SupervisorState) -> dict[str, Any]:
        root_id = state["root_session_id"]
        events = state.get("claimed_events", [])
        current = list(state.get("current_events", []))
        incorporated = list(events)
        seen_ids = {event.get("id") for event in current}
        current.extend(event for event in incorporated if event.get("id") not in seen_ids)
        current = current[-limit:] if limit > 0 else current
        last_event = incorporated[-1] if incorporated else None
        pending_intervention = state.get("pending_intervention")
        original_task = state.get("original_task")
        for event in incorporated:
            payload = event.get("payload")
            if isinstance(payload, dict) and isinstance(payload.get("intervention"), dict):
                pending_intervention = payload["intervention"]
            if event.get("type") == "USER_MESSAGE_FINAL" and not original_task:
                original_task = _event_text(event)
        progress_events = [event for event in incorporated if event.get("type") in PROGRESS_EVENTS]
        progress_event = progress_events[-1] if progress_events else None
        progress_at = _timestamp((progress_event or {}).get("received_at")) if progress_event else None
        previous_progress = _timestamp(state.get("last_progress_at"))
        progress_count_increment = len(progress_events)
        if progress_at and (previous_progress is None or progress_at >= previous_progress):
            last_progress_at = progress_at.isoformat()
            progress_count = int(state.get("progress_count", 0)) + progress_count_increment
            last_progress_kind = progress_event.get("type")
        else:
            last_progress_at = state.get("last_progress_at")
            progress_count = int(state.get("progress_count", 0))
            last_progress_kind = state.get("last_progress_kind")
        activity_started_at = state.get("activity_started_at") or (
            _timestamp((incorporated[0] if incorporated else {}).get("received_at")).isoformat()
            if incorporated and _timestamp((incorporated[0] if incorporated else {}).get("received_at")) else None
        )
        now_monotonic = time.monotonic()
        emit(logger, logging.INFO, "inbox_incorporated", node="READ_INBOX", root_session_id=root_id,
             count=len(incorporated), last_event_id=last_event.get("id") if last_event else None)
        return {
            "current_events": current,
            "last_seen_event_id": last_event["id"] if last_event else state.get("last_seen_event_id"),
            "current_activity": f"incorporated {len(incorporated)} event(s)",
            "current_tool": next((event["type"] for event in reversed(incorporated) if "TOOL" in event["type"]), state.get("current_tool")),
            "processing_events": incorporated,
            "claimed_events": [],
            "pending_intervention": pending_intervention,
            "original_task": original_task,
            "activity_started_at": activity_started_at,
            "last_progress_at": last_progress_at,
            "progress_count": progress_count,
            "last_progress_kind": last_progress_kind,
            "activity_started_monotonic": state.get("activity_started_monotonic") or now_monotonic,
            "last_progress_monotonic": now_monotonic if progress_events else state.get("last_progress_monotonic"),
            "supervisor_tick": False,
            "memory_operation": None,
            "memory_operation_result": None,
            "memory_link_request": None,
            "memory_read_status": "NOT_STARTED" if state.get("memory_read_status") == "FAILED" else state.get("memory_read_status", "NOT_STARTED"),
            "memory_read_error": None if state.get("memory_read_status") == "FAILED" else state.get("memory_read_error"),
            "next_action": "REVIEW",
        }

    return read_inbox
