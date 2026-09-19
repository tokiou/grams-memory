from __future__ import annotations

from typing import Any
from supervisor.agent.nodes.common import cycle_key

from supervisor.agent.schemas import validate_memory_proposal
from supervisor.memory.client import _field


def _identity(category: str, title: str, content: str) -> tuple[str, str, str]:
    return category, title.strip().casefold(), content.strip().casefold()


def make_apply_memory_update(memory):
    async def node(state):
        proposal = validate_memory_proposal(
            state.get("proposed_memory_update", {"memories": [], "relations": []})
        )
        context = state.get("process_context") or {}
        category_ids = context.get("category_ids") or context.get("key", {}).get("category_ids") or {}
        if not all(isinstance(category_ids.get(name), str) and category_ids[name] for name in ("STRATEGY", "EVIDENCE")):
            raise ValueError("process context has no writable STRATEGY and EVIDENCE categories")

        existing_by_identity: dict[tuple[str, str, str], str] = {}
        cycle_refs: dict[str, tuple[str, tuple[str, str, str]]] = {}
        marker = cycle_key(state) if state.get("claimed_events") else None
        scoped_ids: set[str] = set()
        for category, values in (context.get("categories") or {}).items():
            for value in values:
                memory_id = _field(value, "id")
                value_category_id = str(_field(value, "category_id") or "")
                if value_category_id != str(category_ids.get(category, "")):
                    raise ValueError("process context contains memory outside its declared category")
                if not memory_id:
                    raise ValueError("process context contains a memory without an id")
                scoped_ids.add(str(memory_id))
                existing_by_identity[_identity(
                    category,
                    str(_field(value, "title") or ""),
                    str(_field(value, "content") or ""),
                )] = str(memory_id)
                description = str(_field(value, "description") or "")
                if marker and description.startswith(f"{marker}:"):
                    cycle_refs[description.removeprefix(f"{marker}:")] = (
                        str(memory_id),
                        _identity(
                            category,
                            str(_field(value, "title") or ""),
                            str(_field(value, "content") or ""),
                        ),
                    )

        refs = {candidate["candidate_ref"] for candidate in proposal["memories"]}
        for relation in proposal["relations"]:
            for endpoint in (relation["source_id"], relation["target_id"]):
                if endpoint not in refs and endpoint not in scoped_ids:
                    raise ValueError(f"relation endpoint {endpoint} is outside the current process scope")

        existing_relations = {
            (
                str(_field(edge, "source_id") or ""),
                str(_field(edge, "relation") or ""),
                str(_field(edge, "target_id") or ""),
            )
            for edge in context.get("relations") or []
        }
        resolved: dict[str, str] = {}
        created_ids: list[str] = []
        reused_ids: list[str] = []
        for candidate in proposal["memories"]:
            identity = _identity(candidate["category"], candidate["title"], candidate["content"])
            tagged = cycle_refs.get(candidate["candidate_ref"])
            if tagged and tagged[1] != identity:
                raise RuntimeError("regenerated memory proposal conflicts with the durable cycle proposal")
            existing_id = tagged[0] if tagged else existing_by_identity.get(identity)
            if existing_id:
                resolved[candidate["candidate_ref"]] = existing_id
                reused_ids.append(existing_id)
                continue
            category_id = category_ids[candidate["category"]]
            payload = {
                "category_id": category_id,
                "title": candidate["title"],
                "content": candidate["content"],
                "source": "supervisor",
            }
            if marker:
                payload["description"] = f"{marker}:{candidate['candidate_ref']}"
            created = await memory.create(payload)
            memory_id = _field(created, "id")
            if not memory_id or str(_field(created, "category_id")) != category_id:
                raise RuntimeError("Memory MCP returned an invalid created memory")
            resolved[candidate["candidate_ref"]] = str(memory_id)
            scoped_ids.add(str(memory_id))
            created_ids.append(str(memory_id))
            existing_by_identity[identity] = str(memory_id)

        created_relations = []
        skipped_relations = []
        for relation in proposal["relations"]:
            source = resolved.get(relation["source_id"], relation["source_id"])
            target = resolved.get(relation["target_id"], relation["target_id"])
            identity = (source, relation["relation_type"], target)
            if identity in existing_relations:
                skipped_relations.append(identity)
                continue
            created_relations.append(await memory.link(source, target, relation["relation_type"], source="supervisor"))
            existing_relations.add(identity)
        return {
            "memory_update_result": {
                "created_memory_ids": created_ids,
                "reused_memory_ids": list(dict.fromkeys(reused_ids)),
                "created_relations": created_relations,
                "skipped_relations": skipped_relations,
                "resolved_refs": resolved,
            },
            "context_reload_reason": "memory_update",
        }

    return node
