"""Outbound intervention node."""

from datetime import datetime, timezone
from typing import Any
import logging

from ...opencode import OpenCodeClient
from ..state import SupervisorState
from ...observability import emit

logger = logging.getLogger(__name__)


def make_intervene_node(opencode: OpenCodeClient):
    async def intervene(state: SupervisorState) -> dict[str, Any]:
        intervention_at = datetime.now(timezone.utc).isoformat()
        request = state.get("pending_intervention") or {}
        session_id = state.get("root_session_id", "default")
        action = request.get("action", "message")
        if not session_id or session_id == "default":
            emit(logger, logging.WARNING, "intervention_skipped", node="INTERVENE", action=action,
                 reason="no_real_session")
            return {
                "pending_intervention": None,
                "last_intervention": {
                    "action": action,
                    "session_id": session_id,
                    "skipped": True,
                },
                "last_intervention_at": intervention_at,
                "next_action": "REVIEW",
            }
        if action == "abort":
            result = await opencode.abort_session(session_id)
        elif action == "task_control":
            result = await opencode.task_control(session_id, str(request.get("command", "pause")), **dict(request.get("arguments", {})))
        else:
            result = await opencode.send_message(session_id, str(request.get("message", "Supervisor intervention")))
        emit(logger, logging.INFO, "intervention_completed", node="INTERVENE",
             root_session_id=session_id, action=action)
        return {
            "pending_intervention": None,
            "last_intervention": {"action": action, "session_id": session_id, "result": result},
            "last_intervention_at": intervention_at,
            "next_action": "REVIEW",
        }

    return intervene
