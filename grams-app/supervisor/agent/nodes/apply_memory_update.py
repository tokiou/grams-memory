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
                    identity = _identity(
                        category,
                        str(_field(value, "title") or ""),
                        str(_field(value, "content") or ""),
                    )
                    for candidate_ref in description.removeprefix(f"{marker}:").split(","):
                        if candidate_ref:
                            cycle_refs[candidate_ref] = (str(memory_id), identity)

        pagination = context.get("category_pagination") or {}
        needs_tag_reconciliation = any(
            isinstance(value, dict) and value.get("truncated") is True
            for value in pagination.values()
        )
        if marker and proposal["memories"] and needs_tag_reconciliation and hasattr(memory, "search"):
            process = context.get("process") or {}
            key = context.get("key") or {}
            key_id = str(_field(key, "id") or "")
            project_id = str(_field(process, "project_id") or "")
            for category in ("STRATEGY", "EVIDENCE"):
                category_id = category_ids[category]
                if not key_id:
                    continue
                search_filters = {"key_id": key_id, "category_id": category_id, "query": marker, "limit": 0}
                if project_id:
                    search_filters["project_id"] = project_id
                for value in await memory.search(**search_filters):
                    if not isinstance(value, dict):
                        continue
                    memory_id = _field(value, "id")
                    if str(_field(value, "category_id") or "") != str(category_id):
                        continue
                    description = str(_field(value, "description") or "")
                    if not memory_id or not description.startswith(f"{marker}:"):
                        continue
                    identity = _identity(
                        category,
                        str(_field(value, "title") or ""),
                        str(_field(value, "content") or ""),
                    )
                    for candidate_ref in description.removeprefix(f"{marker}:").split(","):
                        if candidate_ref:
                            cycle_refs[candidate_ref] = (str(memory_id), identity)
                    existing_by_identity[identity] = str(memory_id)

        existing_relations = {
            (
                str(_field(edge, "source_id") or ""),
                str(_field(edge, "relation") or ""),
                str(_field(edge, "target_id") or ""),
            )
            for edge in context.get("relations") or []
        }
        # Resolve all candidates and relations before any MCP mutation. This
        # prevents deduplication from turning a valid-looking relation into a
        # self-edge after earlier candidates have already been created.
        canonical_targets: dict[str, tuple[str, Any]] = {}
        planned_groups: dict[tuple[str, str, str], dict[str, Any]] = {}
        planned_group_refs: dict[tuple[str, str, str], list[str]] = {}
        for candidate in proposal["memories"]:
            identity = _identity(candidate["category"], candidate["title"], candidate["content"])
            tagged = cycle_refs.get(candidate["candidate_ref"])
            if tagged and tagged[1] != identity:
                raise RuntimeError("regenerated memory proposal conflicts with the durable cycle proposal")
            if tagged:
                canonical_targets[candidate["candidate_ref"]] = ("existing", tagged[0])
            elif identity in existing_by_identity:
                canonical_targets[candidate["candidate_ref"]] = ("existing", existing_by_identity[identity])
            else:
                planned_groups.setdefault(identity, candidate)
                planned_group_refs.setdefault(identity, []).append(candidate["candidate_ref"])
                canonical_targets[candidate["candidate_ref"]] = ("planned", identity)

        checked_endpoints: dict[str, tuple[str, Any] | None] = {}

        async def resolve_endpoint(endpoint: str) -> tuple[str, Any]:
            if endpoint in checked_endpoints:
                cached = checked_endpoints[endpoint]
                if cached is not None:
                    return cached
                raise ValueError(f"relation endpoint {endpoint} is outside the current process scope")
            if endpoint in canonical_targets:
                return canonical_targets[endpoint]
            if endpoint in scoped_ids:
                return ("existing", endpoint)
            if hasattr(memory, "get"):
                value = await memory.get(endpoint)
                if (
                    isinstance(value, dict)
                    and str(_field(value, "id") or "") == endpoint
                    and str(_field(value, "category_id") or "") in category_ids.values()
                ):
                    checked_endpoints[endpoint] = ("existing", endpoint)
                    scoped_ids.add(endpoint)
                    return ("existing", endpoint)
            checked_endpoints[endpoint] = None
            raise ValueError(f"relation endpoint {endpoint} is outside the current process scope")

        planned_relations = []
        omitted_relation_plans = []
        for relation in proposal["relations"]:
            source = await resolve_endpoint(relation["source_id"])
            target = await resolve_endpoint(relation["target_id"])
            if source == target:
                omitted_relation_plans.append((relation, source))
                continue
            planned_relations.append((relation, source, target))

        resolved: dict[str, str] = {
            candidate_ref: target[1]
            for candidate_ref, target in canonical_targets.items()
            if target[0] == "existing"
        }
        created_ids: list[str] = []
        reused_ids = [target[1] for target in canonical_targets.values() if target[0] == "existing"]
        for identity, candidate in planned_groups.items():
            category_id = category_ids[candidate["category"]]
            payload = {
                "category_id": category_id,
                "title": candidate["title"],
                "content": candidate["content"],
                "source": "supervisor",
            }
            if marker:
                payload["description"] = f"{marker}:{','.join(planned_group_refs[identity])}"
            created = await memory.create(payload)
            memory_id = _field(created, "id")
            if not memory_id or str(_field(created, "category_id")) != category_id:
                raise RuntimeError("Memory MCP returned an invalid created memory")
            if str(memory_id) in scoped_ids:
                raise RuntimeError("Memory MCP returned a duplicate created memory id")
            for candidate_ref, target in canonical_targets.items():
                if target == ("planned", identity):
                    resolved[candidate_ref] = str(memory_id)
            scoped_ids.add(str(memory_id))
            created_ids.append(str(memory_id))
            existing_by_identity[identity] = str(memory_id)

        omitted_relations = [{
            **relation,
            "resolved_id": str(resolved.get(relation["source_id"], source_plan[1])),
            "reason": "deduplication_self_relation",
        } for relation, source_plan in omitted_relation_plans]
        created_relations = []
        skipped_relations = []
        for relation, source_plan, target_plan in planned_relations:
            source = resolved.get(relation["source_id"], source_plan[1])
            target = resolved.get(relation["target_id"], target_plan[1])
            if source == target:
                omitted_relations.append({
                    **relation,
                    "resolved_id": str(source),
                    "reason": "deduplication_self_relation",
                })
                continue
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
                "omitted_relations": omitted_relations,
                "resolved_refs": resolved,
            },
            "context_reload_reason": "memory_update",
        }

    return node
