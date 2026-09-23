from __future__ import annotations

import json
import os
import logging
from typing import Any

from supervisor.agent.nodes.common import cycle_key
from supervisor.agent.prompts import format_intervention_for_agent
from supervisor.agent.state import SupervisorState
from supervisor.interventions import InterventionStatus
from supervisor.interventions.repository import PendingInterventionRepository
from supervisor.memory.client import MemoryClient, _field
from supervisor.opencode.client import OpenCodeClient
from supervisor.observability import emit

logger = logging.getLogger(__name__)


def _contains_message(value: Any, message: str) -> bool:
    if isinstance(value, str):
        return value.strip() == message.strip()
    if isinstance(value, dict):
        return any(_contains_message(item, message) for item in value.values())
    if isinstance(value, list):
        return any(_contains_message(item, message) for item in value)
    return False


def _content(status: str, message: str) -> str:
    return f"{status}\n{message.strip()}"


def _message_from_audit(value: Any) -> str | None:
    content = str(_field(value, "content") or "")
    if "\n" not in content:
        return None
    message = content.split("\n", 1)[1].strip()
    return message or None


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


def make_send_intervention(
    opencode: OpenCodeClient,
    memory: MemoryClient,
    *,
    pending_interventions: PendingInterventionRepository | None = None,
    fallback_mode: str | None = None,
):
    async def node(state: SupervisorState):
        session_id = state.get("root_session_id")
        message = state.get("intervention_message")
        category_id = state.get("process_context", {}).get("key", {}).get("evidence_category_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("root_session_id is required to send an intervention")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("intervention_message is required")
        if not category_id:
            raise ValueError("evidence_category_id is required to persist intervention intent")

        marker = cycle_key(state)
        title = f"Supervisor intervention [{marker}]"
        description = _audit_description(state)
        existing = await memory.search(category_id=category_id, query=marker, limit=10)
        audit = next((item for item in existing if str(_field(item, "title") or "") == title), None)
        durable_message = (
            _message_from_audit(audit)
            if audit is not None
            else format_intervention_for_agent(message)
        )
        if not durable_message:
            durable_message = message.strip()

        # Keep the old transport available for isolated callers that have not
        # been wired to the durable queue yet. The application always supplies
        # the repository, so this is compatibility-only behavior.
        if pending_interventions is None:
            selected_fallback = fallback_mode or os.getenv(
                "GRAMS_INTERVENTION_FALLBACK", "system_transform"
            ).strip().lower()
            if selected_fallback != "prompt_async":
                raise RuntimeError(
                    "pending intervention repository is required unless prompt_async fallback is explicit"
                )
            had_intent = audit is not None
            if audit is None:
                audit = await memory.create({
                    "category_id": category_id,
                    "title": title,
                    "content": _content("PREPARED", durable_message),
                    "description": description,
                    "source": "supervisor",
                })
            audit_id = _field(audit, "id")
            if not audit_id:
                raise RuntimeError("persisted intervention intent has no ID")
            content = str(_field(audit, "content") or "")
            delivered = content.startswith("DELIVERED\n")
            sending = content.startswith("SENDING\n")
            unknown = content.startswith("DELIVERY_UNKNOWN\n")
            if had_intent and not (delivered or sending or unknown or content.startswith("PREPARED\n")):
                raise RuntimeError("persisted intervention intent has an invalid state")
            durable_message = _message_from_audit(audit) or durable_message
            if delivered or unknown:
                return {"intervention_result": {
                    "delivered": delivered,
                    "delivery_status": "DELIVERED" if delivered else "UNKNOWN",
                    "response": None,
                    "message": durable_message,
                    "session_id": session_id,
                    "delivery_key": marker,
                    "audit_recorded": True,
                    "audit_memory_id": str(audit_id),
                }}
            if sending:
                try:
                    context = await opencode.get_context(session_id)
                except Exception:
                    context = {}
                if _contains_message(context, durable_message):
                    await memory.update(str(audit_id), {
                        "title": title,
                        "content": _content("DELIVERED", durable_message),
                        "description": description,
                        "source": "supervisor",
                    })
                    return {"intervention_result": {
                        "delivered": True,
                        "delivery_status": "DELIVERED",
                        "response": None,
                        "message": durable_message,
                        "session_id": session_id,
                        "delivery_key": marker,
                        "audit_recorded": True,
                        "audit_memory_id": str(audit_id),
                    }}
                await memory.update(str(audit_id), {
                    "title": title,
                    "content": _content("DELIVERY_UNKNOWN", durable_message),
                    "description": description,
                    "source": "supervisor",
                })
                return {"intervention_result": {
                    "delivered": False,
                    "delivery_status": "UNKNOWN",
                    "response": None,
                    "message": durable_message,
                    "session_id": session_id,
                    "delivery_key": marker,
                    "audit_recorded": True,
                    "audit_memory_id": str(audit_id),
                }}
            await memory.update(str(audit_id), {
                "title": title,
                "content": _content("SENDING", durable_message),
                "description": description,
                "source": "supervisor",
            })
            response = await opencode.send_message(session_id, durable_message)
            await memory.update(str(audit_id), {
                "title": title,
                "content": _content("DELIVERED", durable_message),
                "description": description,
                "source": "supervisor",
            })
            return {"intervention_result": {
                "delivered": True,
                "delivery_status": "FALLBACK_DELIVERED",
                "response": response,
                "message": durable_message,
                "session_id": session_id,
                "delivery_key": marker,
                "audit_recorded": True,
                "audit_memory_id": str(audit_id),
            }}

        selected_fallback = fallback_mode or os.getenv(
            "GRAMS_INTERVENTION_FALLBACK", "system_transform"
        ).strip().lower()
        if selected_fallback not in {"system_transform", "prompt_async"}:
            raise ValueError("intervention fallback must be system_transform or prompt_async")
        pending = await pending_interventions.enqueue(
            session_id,
            durable_message,
            marker,
            initial_status=(
                InterventionStatus.FALLBACK_SENDING
                if selected_fallback == "prompt_async"
                else InterventionStatus.PENDING
            ),
        )
        pending_id = str(pending.get("id") or "")
        created = pending.get("created") is True
        fallback_token = str(pending.get("fallback_token") or "")
        durable_message = str(pending.get("message") or durable_message)
        status = str(pending.get("status") or InterventionStatus.PENDING)
        delivered = status in {
            InterventionStatus.CONSUMED,
            InterventionStatus.FALLBACK_DELIVERED,
        }
        delivery_status = (
            "CONSUMED" if status == InterventionStatus.CONSUMED
            else "FALLBACK_DELIVERED" if status == InterventionStatus.FALLBACK_DELIVERED
            else "DELIVERY_UNKNOWN" if status == InterventionStatus.DELIVERY_UNKNOWN
            else "PENDING"
        )
        response: Any = None
        fallback_owner = False

        if selected_fallback == "prompt_async" and status == InterventionStatus.PENDING:
            fallback = await pending_interventions.begin_fallback(pending_id, session_id)
            if fallback is not None:
                status = InterventionStatus.FALLBACK_SENDING
                fallback_token = str(fallback.get("fallback_token") or "")
                fallback_owner = True
            else:
                pending = await pending_interventions.get(pending_id, session_id)
                status = str((pending or {}).get("status") or status)
        elif selected_fallback == "prompt_async" and status == InterventionStatus.FALLBACK_SENDING:
            # A prior owner may have crashed after sending. Do not send again.
            if not created:
                delivery_status = "DELIVERY_UNKNOWN"
            else:
                fallback_owner = True
        if selected_fallback == "prompt_async" and status == InterventionStatus.FALLBACK_SENDING and fallback_owner:
            try:
                response = await opencode.send_message(session_id, durable_message)
            except Exception:
                await pending_interventions.mark_delivery_unknown(
                    pending_id,
                    session_id,
                    fallback_token,
                )
                status = InterventionStatus.DELIVERY_UNKNOWN
                delivery_status = "DELIVERY_UNKNOWN"
            else:
                if not await pending_interventions.mark_fallback_delivered(
                    pending_id,
                    session_id,
                    fallback_token,
                ):
                    raise RuntimeError("pending intervention fallback transition was rejected")
                status = InterventionStatus.FALLBACK_DELIVERED
                delivery_status = "FALLBACK_DELIVERED"
                delivered = True

        emit(
            logger,
            logging.INFO,
            "INTERVENTION_QUEUED",
            session_id=session_id,
            intervention_id=pending_id,
            delivery_key=marker,
            status=status,
        )
        if delivery_status == "FALLBACK_DELIVERED":
            emit(
                logger,
                logging.INFO,
                "INTERVENTION_FALLBACK",
                session_id=session_id,
                intervention_id=pending_id,
                delivery_key=marker,
            )

        if audit is None:
            audit = await memory.create({
                "category_id": category_id,
                "title": title,
                "content": _content(delivery_status, durable_message),
                "description": description,
                "source": "supervisor",
            })
        else:
            audit_id = _field(audit, "id")
            if not audit_id:
                raise RuntimeError("persisted intervention intent has no ID")
            await memory.update(str(audit_id), {
                "title": title,
                "content": _content(delivery_status, durable_message),
                "description": description,
                "source": "supervisor",
            })

        return {"intervention_result": {
            "delivered": delivered,
            "delivery_status": delivery_status,
            "queue_status": status,
            "response": response,
            "message": durable_message,
            "session_id": session_id,
            "delivery_key": marker,
            "pending_intervention_id": pending_id,
            "audit_recorded": True,
            "audit_memory_id": str(_field(audit, "id")) if audit else None,
        }}

    return node
