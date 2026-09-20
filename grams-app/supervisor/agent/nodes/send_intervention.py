from __future__ import annotations

import asyncio
from typing import Any

import httpx

from supervisor.agent.nodes.common import cycle_key
from supervisor.memory.client import _field


def _contains_message(value: Any, message: str) -> bool:
    if isinstance(value, str):
        return value.strip() == message.strip()
    if isinstance(value, dict):
        return any(_contains_message(item, message) for item in value.values())
    if isinstance(value, list):
        return any(_contains_message(item, message) for item in value)
    return False


def _result(
    *,
    delivered: bool,
    delivery_status: str,
    abort_status: str,
    prompt_status: str,
    session_id: str,
    message: str,
    marker: str,
    audit_id: str,
    response: Any = None,
) -> dict[str, Any]:
    return {
        "delivered": delivered,
        "delivery_status": delivery_status,
        "abort_status": abort_status,
        "prompt_status": prompt_status,
        "response": response,
        "session_id": session_id,
        "message": message,
        "delivery_key": marker,
        "audit_recorded": True,
        "audit_memory_id": audit_id,
    }


def make_send_intervention(opencode, memory):
    async def node(state):
        session_id = state.get("root_session_id")
        message = state.get("intervention_message")
        category_id = state.get("process_context", {}).get("key", {}).get("evidence_category_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("root_session_id is required to send an intervention")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("intervention_message is required")
        if not category_id:
            raise ValueError("evidence_category_id is required to persist intervention delivery")

        marker = cycle_key(state)
        title = f"Supervisor intervention [{marker}]"
        failure_title = f"Supervisor intervention failure [{marker}]"
        existing = await memory.search(category_id=category_id, query=marker, limit=10)
        audit = next((item for item in existing if str(_field(item, "title") or "") == failure_title), None)
        if audit is None:
            audit = next((item for item in existing if str(_field(item, "title") or "") == title), None)
        had_intent = audit is not None
        if audit is None:
            audit = await memory.create({
                "category_id": category_id,
                "title": title,
                "content": f"PREPARED\n{message.strip()}",
                "source": "supervisor",
            })
        audit_id = _field(audit, "id")
        if not audit_id or str(_field(audit, "category_id")) != str(category_id):
            raise RuntimeError("Memory MCP returned an invalid intervention intent")
        audit_title = str(_field(audit, "title") or title)

        content = str(_field(audit, "content") or "")
        delivered = content.startswith("DELIVERED\n")
        prepared = content.startswith("PREPARED\n")
        aborting = content.startswith("ABORTING\n")
        abort_failed = content.startswith("ABORT_FAILED\n")
        abort_unknown = content.startswith("ABORT_UNKNOWN\n")
        sending = content.startswith("SENDING\n")
        prompt_failed = content.startswith("PROMPT_FAILED\n")
        unknown = content.startswith("DELIVERY_UNKNOWN\n")
        if had_intent and not (
            delivered or prepared or aborting or abort_failed or abort_unknown or sending or prompt_failed or unknown
        ):
            raise RuntimeError("persisted intervention intent has an invalid state")
        durable_message = content.split("\n", 1)[1] if had_intent else message.strip()
        response: Any = None
        if delivered:
            return {"intervention_result": _result(
                delivered=True,
                delivery_status="DELIVERED",
                abort_status="UNKNOWN",
                prompt_status="DELIVERED",
                session_id=session_id,
                message=durable_message,
                marker=marker,
                audit_id=str(audit_id),
            )}
        if unknown:
            try:
                context = await opencode.get_context(session_id)
            except Exception:
                context = {}
            if _contains_message(context, durable_message):
                await memory.update(str(audit_id), {
                    "title": audit_title,
                    "content": f"DELIVERED\n{durable_message}",
                    "source": "supervisor",
                })
                return {"intervention_result": _result(
                    delivered=True,
                    delivery_status="DELIVERED",
                    abort_status="UNKNOWN",
                    prompt_status="DELIVERED",
                    session_id=session_id,
                    message=durable_message,
                    marker=marker,
                    audit_id=str(audit_id),
                )}
            return {"intervention_result": _result(
                delivered=False,
                delivery_status="UNKNOWN",
                abort_status="UNKNOWN",
                prompt_status="UNKNOWN",
                session_id=session_id,
                message=durable_message,
                marker=marker,
                audit_id=str(audit_id),
            )}
        if sending:
            for attempt in range(3):
                try:
                    context = await opencode.get_context(session_id)
                except Exception:
                    context = {}
                if _contains_message(context, durable_message):
                    delivered = True
                    break
                if attempt < 2:
                    await asyncio.sleep(0.1 * (attempt + 1))
            if not delivered:
                await memory.update(str(audit_id), {
                    "title": audit_title,
                    "content": f"DELIVERY_UNKNOWN\n{durable_message}",
                    "source": "supervisor",
                })
                return {"intervention_result": _result(
                    delivered=False,
                    delivery_status="UNKNOWN",
                    abort_status="CONFIRMED",
                    prompt_status="UNKNOWN",
                    session_id=session_id,
                    message=durable_message,
                    marker=marker,
                    audit_id=str(audit_id),
                )}
        if prompt_failed:
            raise RuntimeError("persisted intervention prompt failed")
        abort_status = "CONFIRMED" if sending else "UNKNOWN"
        if not delivered:
            await memory.update(str(audit_id), {
                "title": audit_title,
                "content": f"ABORTING\n{durable_message}",
                "source": "supervisor",
            })
            try:
                await opencode.abort_session(session_id)
            except Exception as error:
                failure_state = "ABORT_FAILED" if isinstance(error, httpx.HTTPStatusError) else "ABORT_UNKNOWN"
                await memory.update(str(audit_id), {
                    "title": audit_title,
                    "content": f"{failure_state}\n{durable_message}",
                    "source": "supervisor",
                })
                raise
            abort_status = "CONFIRMED"
        if not delivered:
            await memory.update(str(audit_id), {
                "title": audit_title,
                "content": f"SENDING\n{durable_message}",
                "source": "supervisor",
            })
            try:
                response = await opencode.send_message(session_id, durable_message)
            except httpx.HTTPStatusError as error:
                try:
                    await memory.update(str(audit_id), {
                        "title": audit_title,
                        "content": f"PROMPT_FAILED\n{durable_message}",
                        "source": "supervisor",
                    })
                except Exception as persist_error:
                    fallback = await memory.create({
                        "category_id": category_id,
                        "title": failure_title,
                        "content": f"PROMPT_FAILED\n{durable_message}",
                        "source": "supervisor",
                    })
                    fallback_id = _field(fallback, "id")
                    if not fallback_id or str(_field(fallback, "category_id")) != str(category_id):
                        raise RuntimeError("Memory MCP could not persist prompt failure") from persist_error
                raise
        await memory.update(str(audit_id), {
            "title": audit_title,
            "content": f"DELIVERED\n{durable_message}",
            "source": "supervisor",
        })
        return {"intervention_result": _result(
            delivered=True,
            delivery_status="DELIVERED",
            abort_status=abort_status,
            prompt_status="DELIVERED",
            session_id=session_id,
            message=durable_message,
            marker=marker,
            audit_id=str(audit_id),
            response=response,
        )}

    return node
