from __future__ import annotations

from supervisor.agent.nodes.common import cycle_key
from supervisor.agent.prompts import MEMORY_MATERIALIZATION_SYSTEM_PROMPT
from supervisor.agent.schemas import (
    MEMORY_MATERIALIZATION_JSON_SCHEMA,
    validate_materializations,
    validate_memory_proposal,
)
from supervisor.agent.state_builder import build_jev_process_state


def make_materialize_memories(openrouter):
    async def node(state):
        candidates = list(state.get("memory_candidates") or [])
        curation = state.get("memory_curation") or {"decisions": [], "relations": []}
        decisions = {item["candidate_ref"]: item for item in curation["decisions"]}
        kept = {ref for ref, decision in decisions.items() if decision["keep"]}
        all_candidate_refs = {candidate["candidate_ref"] for candidate in candidates}
        materialization_curation = {
            "decisions": [decision for decision in curation["decisions"] if decision["candidate_ref"] in kept],
            "relations": [
                relation for relation in curation["relations"]
                if not (
                    (relation["source_ref"] in all_candidate_refs and relation["source_ref"] not in kept)
                    or (relation["target_ref"] in all_candidate_refs and relation["target_ref"] not in kept)
                )
            ],
        }
        if not kept:
            return {
                "memory_materializations": [],
                "proposed_memory_update": {"memories": [], "relations": []},
            }
        value = await openrouter.generate_json(
            operation="MATERIALIZE_MEMORIES",
            payload={
                **build_jev_process_state(state),
                "memory_candidates": [candidate for candidate in candidates if candidate["candidate_ref"] in kept],
                "memory_curation": materialization_curation,
            },
            system_prompt=MEMORY_MATERIALIZATION_SYSTEM_PROMPT,
            schema=MEMORY_MATERIALIZATION_JSON_SCHEMA,
        )
        materializations = validate_materializations(value, kept)
        materialized_by_ref = {item["candidate_ref"]: item for item in materializations}
        candidate_by_ref = {item["candidate_ref"]: item for item in candidates}
        memories = []
        for ref in sorted(kept, key=lambda item: int(item.removeprefix("new_"))):
            decision = decisions[ref]
            candidate = candidate_by_ref[ref]
            materialization = materialized_by_ref[ref]
            memories.append({
                "category": decision["category"],
                "title": materialization["title"],
                "content": materialization["content"],
                "candidate_ref": ref,
                "role": decision["role"],
                "status": decision["status"],
                "confidence": decision["confidence"],
                "progress_effect": decision["progress_effect"],
                "importance": decision["importance"],
                "provenance": candidate["provenance"],
            })
        relations = []
        for relation in curation["relations"]:
            if (
                relation["source_ref"] in all_candidate_refs and relation["source_ref"] not in kept
            ) or (
                relation["target_ref"] in all_candidate_refs and relation["target_ref"] not in kept
            ):
                continue
            relations.append({
                "source_id": relation["source_ref"],
                "relation_type": relation["relation_type"],
                "target_id": relation["target_ref"],
                **{key: relation[key] for key in ("confidence", "evidence_strength", "direct") if key in relation},
            })
        proposal = validate_memory_proposal({"memories": memories, "relations": relations})
        return {
            "memory_materializations": materializations,
            "memory_curation": materialization_curation,
            "proposed_memory_update": proposal,
        }

    return node
