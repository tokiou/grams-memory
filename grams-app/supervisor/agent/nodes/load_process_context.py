"""Load the current process graph context from Memory MCP."""

from typing import Any

from supervisor.agent.state import SupervisorState
from supervisor.memory.client import MemoryClient, _field


def _find_process_key(manifest: dict[str, Any], project_id: str, key_id: str) -> dict[str, Any]:
    for project in manifest.get("projects", []):
        if str(_field(project, "id")) != project_id:
            continue
        for key in project.get("keys", []):
            if str(_field(key, "id")) == key_id:
                return key
    raise RuntimeError(f"Memory MCP key {key_id} not found in project {project_id}")


def make_load_process_context(memory: MemoryClient):
    async def load_process_context(state: SupervisorState) -> dict[str, Any]:
        project_id = state.get("project_id")
        process_id = state.get("active_process_id")
        if not project_id or not process_id:
            raise ValueError("project_id and active_process_id are required to load process context")

        process = await memory.get_process(process_id)
        if process is None:
            raise RuntimeError(f"Memory MCP process {process_id} was not found")
        if str(_field(process, "project_id")) != project_id:
            raise RuntimeError(f"Memory MCP process {process_id} belongs to another project")
        key_id = _field(process, "key_id")
        if not key_id:
            raise RuntimeError(f"Memory MCP process {process_id} has no memory key")

        key = _find_process_key(await memory.get_manifest(), project_id, str(key_id))
        categories: dict[str, list[dict[str, Any]]] = {}
        for category in key.get("categories", []):
            category_name = str(_field(category, "name") or "").upper()
            if category_name not in {"STRATEGY", "EVIDENCE", "SUMMARY"}:
                continue
            category_id = _field(category, "id")
            if not category_id:
                raise RuntimeError(f"Memory MCP category {category_name} has no id")
            categories[category_name] = await memory.search(category_id=str(category_id), limit=50)

        for category_name in ("STRATEGY", "EVIDENCE", "SUMMARY"):
            categories.setdefault(category_name, [])

        recent_changes = [
            memory_item
            for items in categories.values()
            for memory_item in items
        ]
        recent_changes.sort(key=lambda item: str(_field(item, "updated_at") or _field(item, "created_at") or ""), reverse=True)
        context = {
            "process": process,
            "key": key,
            "categories": categories,
            "recent_changes": recent_changes[:20],
        }
        return {"process_context": context}

    return load_process_context


async def load_process_context(state: SupervisorState) -> dict:
    """Load a compact, current view of the ACTIVE process graph.

    Future implementation: read process identity, status, STRATEGY, EVIDENCE,
    SUMMARY, relevant relations, and recent changes from Memory MCP. This node
    is reused before continuity assessment, after memory updates, and after a
    new process starts. It must not use an LLM.
    """
    raise RuntimeError("load_process_context requires a Memory MCP client; use make_load_process_context")
