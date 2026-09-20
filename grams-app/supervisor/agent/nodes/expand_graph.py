from __future__ import annotations

from typing import Any

from supervisor.agent.nodes.load_process_context import load_process_context
from supervisor.agent.schemas import RELATION_TYPES
from supervisor.memory.client import _field


def _scoped_memory_ids(state: dict[str, Any]) -> set[str]:
    categories = (state.get("process_context") or {}).get("categories") or {}
    return {
        str(memory_id)
        for values in categories.values()
        for memory in values
        if (memory_id := _field(memory, "id"))
    }


def make_expand_graph(memory, *, max_depth=3):
    if max_depth < 1:
        raise ValueError("max_depth must be positive")
    if max_depth > 3:
        raise ValueError("max_depth cannot exceed 3")

    async def node(state):
        raw_depth = state.get("memory_expansion_depth", 0)
        if not isinstance(raw_depth, int) or isinstance(raw_depth, bool) or raw_depth < 0:
            raise ValueError("memory_expansion_depth must be an integer from zero to three")
        previous_depth = raw_depth
        if previous_depth > 3:
            raise ValueError("memory_expansion_depth cannot exceed three")
        if previous_depth >= max_depth:
            return {"memory_expansion_exhausted": True, "memory_expansion_depth": previous_depth}
        depth = previous_depth + 1
        decision = state.get("supervision_decision") or {}
        if decision.get("action") != "NEED_MORE_MEMORY":
            raise ValueError("EXPAND_GRAPH requires a NEED_MORE_MEMORY decision")

        scoped_ids = _scoped_memory_ids(state)
        requested_ids = list(dict.fromkeys(decision.get("memory_ids") or []))
        if any(memory_id not in scoped_ids for memory_id in requested_ids):
            raise ValueError("memory expansion requested an id outside the current process")
        relations = list(dict.fromkeys(decision.get("relation_types") or []))
        if any(relation not in RELATION_TYPES for relation in relations):
            raise ValueError("memory expansion requested an unsupported relation type")

        expanded = dict(state.get("expanded_memory_context") or {})
        memories = dict(expanded.get("memories") or {})
        subgraphs = dict(expanded.get("subgraphs") or {})
        category_pages = dict(expanded.get("category_pages") or {})
        category_page_exhausted = dict(expanded.get("category_page_exhausted") or {})

        context = state.get("process_context") or {}
        pagination = context.get("category_pagination") or {}
        category_ids = context.get("category_ids") or {}
        key_id = _field(context.get("process") or {}, "key_id")
        paged_values: dict[str, dict[str, Any]] = {}
        for category, page in pagination.items():
            if (
                not isinstance(page, dict)
                or not page.get("truncated")
                or category_page_exhausted.get(category)
            ):
                continue
            category_id = category_ids.get(category)
            if not category_id:
                raise RuntimeError(f"missing category id for paginated {category} memories")
            offset = int(page.get("next_offset") or 50) + ((depth - 1) * 50)
            values = await memory.search(
                project_id=state.get("project_id"),
                key_id=key_id,
                category_id=category_id,
                limit=50,
                offset=offset,
            )
            for value in values:
                memory_id = _field(value, "id")
                if not memory_id or str(_field(value, "category_id")) != str(category_id):
                    raise RuntimeError("paginated memory escaped the requested process category")
                paged_values[str(memory_id)] = value
            existing = {
                str(_field(item, "id")): item
                for item in category_pages.get(category, [])
                if _field(item, "id")
            }
            for value in values:
                existing.setdefault(str(_field(value, "id")), value)
            category_pages[category] = list(existing.values())
            category_page_exhausted[category] = len(values) < 50

        seeds = list(dict.fromkeys([
            *(requested_ids or sorted(scoped_ids)),
            *paged_values,
        ]))
        for memory_id in seeds:
            value = paged_values.get(memory_id) or await memory.get(memory_id)
            if not isinstance(value, dict):
                raise RuntimeError(f"requested memory {memory_id} was not found")
            memories[memory_id] = value
            subgraphs[memory_id] = await memory.neighbors(
                memory_id,
                depth=depth,
                **({"relations": relations} if relations else {}),
            )

        project_id = state.get("project_id")
        if not project_id:
            raise ValueError("project_id is required for graph expansion")
        allowed_processes = {
            str(_field(process, "id")): process
            for process in await memory.list_processes(project_id)
            if _field(process, "id")
        }
        requested_process_ids = list(dict.fromkeys(decision.get("related_process_ids") or []))
        if state.get("active_process_id") in requested_process_ids:
            raise ValueError("related process expansion cannot request the active process")
        if any(process_id not in allowed_processes for process_id in requested_process_ids):
            raise ValueError("related process expansion escaped the current project")
        related_processes = dict(expanded.get("related_processes") or {})
        summaries = dict(expanded.get("summaries") or {})
        for process_id in requested_process_ids:
            process_context = await load_process_context(memory, project_id, process_id)
            related_processes[process_id] = process_context["process"]
            if decision.get("summaries") and process_context.get("summary"):
                summaries[process_id] = process_context["summary"]
        current_summary = (state.get("process_context") or {}).get("summary")
        if decision.get("summaries") and current_summary:
            summaries[str(state.get("active_process_id"))] = current_summary

        expanded.update({
            "memories": memories,
            "subgraphs": subgraphs,
            "related_processes": related_processes,
            "summaries": summaries,
            "category_pages": category_pages,
            "category_page_exhausted": category_page_exhausted,
        })
        return {
            "expanded_memory_context": expanded,
            "memory_expansion_depth": depth,
            "memory_expansion_exhausted": False,
        }

    return node
