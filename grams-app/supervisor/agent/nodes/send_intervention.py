from __future__ import annotations

import asyncio
from typing import Any

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
        existing = await memory.search(category_id=category_id, query=marker, limit=10)
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

        content = str(_field(audit, "content") or "")
        delivered = content.startswith("DELIVERED\n")
        prepared = content.startswith("PREPARED\n")
        sending = content.startswith("SENDING\n")
        unknown = content.startswith("DELIVERY_UNKNOWN\n")
        if had_intent and not (delivered or prepared or sending or unknown):
            raise RuntimeError("persisted intervention intent has an invalid state")
        durable_message = content.split("\n", 1)[1] if had_intent else message.strip()
        response: Any = None
        if delivered or unknown:
            return {"intervention_result": {
                "delivered": delivered,
                "delivery_status": "DELIVERED" if delivered else "UNKNOWN",
                "response": None,
                "message": durable_message,
                "delivery_key": marker,
                "audit_recorded": True,
                "audit_memory_id": str(audit_id),
            }}
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
                    "title": title,
                    "content": f"DELIVERY_UNKNOWN\n{durable_message}",
                    "source": "supervisor",
                })
                return {"intervention_result": {
                    "delivered": False,
                    "delivery_status": "UNKNOWN",
                    "response": None,
                    "message": durable_message,
                    "delivery_key": marker,
                    "audit_recorded": True,
                    "audit_memory_id": str(audit_id),
                }}
        if not delivered:
            await memory.update(str(audit_id), {
                "title": title,
                "content": f"SENDING\n{durable_message}",
                "source": "supervisor",
            })
            response = await opencode.send_message(session_id, durable_message)
        await memory.update(str(audit_id), {
            "title": title,
            "content": f"DELIVERED\n{durable_message}",
            "source": "supervisor",
        })
        return {"intervention_result": {
            "delivered": True,
            "delivery_status": "DELIVERED",
            "response": response,
            "message": durable_message,
            "delivery_key": marker,
            "audit_recorded": True,
            "audit_memory_id": str(audit_id),
        }}

    return node
