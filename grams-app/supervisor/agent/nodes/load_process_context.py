from __future__ import annotations

from typing import Any

from supervisor.memory.client import _field


def _normalized(value: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields:
        field_value = _field(value, field)
        if field_value is not None:
            result[field] = field_value
    return result


async def load_process_context(memory, project_id: str, process_id: str) -> dict[str, Any]:
    process_value = await memory.get_process(process_id)
    if not isinstance(process_value, dict):
        raise RuntimeError(f"process {process_id} was not found")
    process = _normalized(process_value, (
        "id", "project_id", "key_id", "name", "status", "predecessor_id", "started_at", "closed_at",
    ))
    if str(process.get("id")) != process_id or str(process.get("project_id")) != project_id:
        raise RuntimeError("Memory MCP returned a process outside the requested scope")
    key_id = process.get("key_id")
    if not key_id:
        raise RuntimeError("active process has no memory key")

    manifest = await memory.get_manifest()
    projects = manifest.get("projects") if isinstance(manifest, dict) else None
    if not isinstance(projects, list):
        raise RuntimeError("Memory MCP returned an invalid manifest")
    project = next((item for item in projects if str(_field(item, "id")) == project_id), None)
    if not isinstance(project, dict):
        raise RuntimeError(f"project {project_id} is missing from the memory manifest")
    keys = project.get("keys")
    key_value = next(
        (item for item in keys if str(_field(item, "id")) == str(key_id)),
        None,
    ) if isinstance(keys, list) else None
    if not isinstance(key_value, dict):
        raise RuntimeError(f"process key {key_id} is missing from the memory manifest")
    if str(_field(key_value, "project_id") or project_id) != project_id:
        raise RuntimeError("process key belongs to the wrong project")

    category_ids: dict[str, str] = {}
    for category in key_value.get("categories") or []:
        name = str(_field(category, "name") or "").upper()
        category_id = _field(category, "id")
        if name in {"STRATEGY", "EVIDENCE", "SUMMARY"} and category_id:
            if name in category_ids:
                raise RuntimeError(f"process key contains duplicate {name} categories")
            category_ids[name] = str(category_id)
    if set(category_ids) != {"STRATEGY", "EVIDENCE", "SUMMARY"}:
        raise RuntimeError("process key must contain STRATEGY, EVIDENCE, and SUMMARY categories")

    categories: dict[str, list[dict[str, Any]]] = {}
    category_pagination: dict[str, dict[str, Any]] = {}
    for name, category_id in category_ids.items():
        values = await memory.search(
            project_id=project_id,
            key_id=str(key_id),
            category_id=category_id,
            limit=51,
        )
        truncated = len(values) > 50
        values = values[:50]
        normalized = []
        for value in values:
            if str(_field(value, "category_id")) != category_id:
                raise RuntimeError("Memory MCP search returned memory outside the requested category")
            normalized.append(_normalized(value, (
                "id", "category_id", "title", "content", "description", "status", "source", "created_at", "updated_at",
            )))
        categories[name] = normalized
        category_pagination[name] = {
            "loaded": len(normalized),
            "truncated": truncated,
            "next_offset": 50 if truncated else None,
        }

    scoped_memories = [item for values in categories.values() for item in values]
    relations: list[dict[str, Any]] = []
    relation_keys: set[tuple[str, str, str]] = set()
    for item in scoped_memories:
        memory_id = item.get("id")
        if not memory_id:
            continue
        subgraph = await memory.neighbors(str(memory_id), depth=1)
        edges = _field(subgraph, "edges") or []
        for edge in edges:
            normalized_edge = _normalized(edge, (
                "id", "source_id", "target_id", "relation", "confidence", "evidence_strength", "direct",
            ))
            identity = (
                str(normalized_edge.get("source_id") or ""),
                str(normalized_edge.get("relation") or ""),
                str(normalized_edge.get("target_id") or ""),
            )
            if all(identity) and identity not in relation_keys:
                relation_keys.add(identity)
                relations.append(normalized_edge)

    processes = await memory.list_processes(project_id)
    related_process_ids = []
    predecessor_id = process.get("predecessor_id")
    if predecessor_id:
        related_process_ids.append(str(predecessor_id))
    for item in processes:
        item_id = _field(item, "id")
        if str(_field(item, "predecessor_id") or "") == process_id and item_id:
            related_process_ids.append(str(item_id))
    related_process_ids = list(dict.fromkeys(related_process_ids))

    recent_changes = sorted(
        scoped_memories,
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )[:20]
    summaries = sorted(
        categories["SUMMARY"],
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    key = _normalized(key_value, ("id", "project_id", "name", "description"))
    key["category_ids"] = category_ids
    key["summary_category_id"] = category_ids["SUMMARY"]
    key["evidence_category_id"] = category_ids["EVIDENCE"]
    return {
        "process": process,
        "key": key,
        "category_ids": category_ids,
        "categories": categories,
        "category_pagination": category_pagination,
        "summary": summaries[0] if summaries else None,
        "relations": relations,
        "related_process_ids": related_process_ids,
        "recent_changes": recent_changes,
    }


def make_load_process_context(memory):
    async def node(state):
        project_id = state.get("project_id")
        process_id = state.get("active_process_id")
        if not project_id or not process_id:
            raise ValueError("project_id and active_process_id are required to load process context")
        reload_reason = state.get("context_reload_reason")
        route = (
            "recover" if state.get("health_route") == "recover"
            else "supervise" if reload_reason == "memory_update"
            else "extract" if reload_reason == "new_process"
            else "assess"
        )
        context = await load_process_context(memory, project_id, process_id)
        return {
            "process_context": context,
            "context_route": route,
            "context_reload_reason": "",
        }

    return node
