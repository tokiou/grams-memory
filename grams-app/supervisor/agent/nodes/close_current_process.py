from __future__ import annotations

from supervisor.agent.nodes.common import cycle_key
from supervisor.agent.services.process_service import ProcessLifecycle
from supervisor.agent.state import SupervisorState
from supervisor.memory.client import MemoryClient, _field


def make_close_current_process(process_service: ProcessLifecycle, memory: MemoryClient):
    async def node(state: SupervisorState):
        summary = state.get("pending_process_summary") or {}
        pivot = state.get("process_continuity", {}).get("decision") == "NEW_PROCESS"
        outcome = "SUPERSEDED" if pivot else summary.get("outcome")
        if summary.get("outcome") != outcome:
            raise ValueError("pending process summary has an inconsistent outcome")
        summary_category_id = state.get("process_context", {}).get("key", {}).get("summary_category_id")
        content = summary.get("content")
        if not summary_category_id or not isinstance(content, str) or not content.strip():
            raise ValueError("a SUMMARY category and content are required before closing a process")

        marker = cycle_key(state)
        title = f"Process summary [{marker}]: {outcome}"
        existing = await memory.search(category_id=summary_category_id, query=marker, limit=50)
        duplicate = next(
            (item for item in existing if str(_field(item, "title") or "").strip() == title),
            None,
        )
        if duplicate is None:
            created = await memory.create({
                "category_id": summary_category_id,
                "title": title,
                "content": content.strip(),
                "source": "supervisor",
            })
            if not _field(created, "id") or str(_field(created, "category_id")) != str(summary_category_id):
                raise RuntimeError("Memory MCP returned an invalid process summary")
        closed = await process_service.close_current(state["active_process_id"], outcome)
        return {"final_status": "CLOSED", "closed_process": closed}

    return node
