from __future__ import annotations

from typing import Any

from supervisor.agent.nodes.common import answers, jev_call, typed_answer
from supervisor.agent.prompts import MEMORY_CURATION_INSTRUCTIONS
from supervisor.agent.schemas import (
    MEMORY_TYPES,
    MEMORY_STATUSES,
    PROGRESS_EFFECTS,
    RELATION_TYPES,
    validate_memory_curation,
)
from supervisor.agent.state_builder import build_jev_process_state
from supervisor.memory.client import _field


def _choice(value: Any, *, expected: set[str] | None = None) -> str:
    answer = typed_answer(value)
    selected = answer["value"]
    if not isinstance(selected, str) or (expected is not None and selected not in expected):
        raise ValueError("Jev returned an invalid curation choice")
    return selected


def _existing_targets(state: dict[str, Any]) -> list[str]:
    context = state.get("process_context") or {}
    targets: list[str] = []
    for category in ("EVIDENCE", "STRATEGY"):
        for memory in (context.get("categories") or {}).get(category) or []:
            memory_id = _field(memory, "id")
            if memory_id and str(memory_id) not in targets:
                targets.append(str(memory_id))
    return targets[:4]


def _relation_options(candidate_refs: list[str], existing_targets: list[str], current_ref: str) -> dict[str, tuple[str, str] | None]:
    options: dict[str, tuple[str, str] | None] = {"NONE": None}
    targets = [ref for ref in candidate_refs if ref != current_ref] + existing_targets
    for target in targets:
        for relation_type in sorted(RELATION_TYPES):
            option = f"{relation_type}::{target}"
            options[option] = (relation_type, target)
    return options


def make_curate_memory_candidates(jev):
    async def node(state):
        candidates = list(state.get("memory_candidates") or [])
        if not candidates:
            return {"memory_curation": {"decisions": [], "relations": []}}

        candidate_refs = [candidate["candidate_ref"] for candidate in candidates]
        existing_targets = _existing_targets(state)
        questions: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            ref = candidate["candidate_ref"]
            questions[f"{ref}_keep"] = {
                "type": "choice",
                "criteria": {"KEEP": "The fact is decision-relevant and should persist.", "DROP": "The fact is routine, duplicate, or low value."},
                "instructions": MEMORY_CURATION_INSTRUCTIONS,
            }
            questions[f"{ref}_category"] = {
                "type": "choice",
                "criteria": {
                    "STRATEGY": "A meaningful plan, approach, decision, pivot, or intentional line of work.",
                    "EVIDENCE": "An observation, discovery, error, measurement, result, validation, blocker, or fact.",
                },
                "instructions": MEMORY_CURATION_INSTRUCTIONS,
            }
            questions[f"{ref}_role"] = {
                "type": "choice",
                "criteria": {role: f"Use the MCP memory type {role}." for role in sorted(MEMORY_TYPES)},
                "instructions": MEMORY_CURATION_INSTRUCTIONS,
            }
            questions[f"{ref}_status"] = {
                "type": "choice",
                "criteria": {status: f"Use the MCP memory status {status}." for status in sorted(MEMORY_STATUSES)},
                "instructions": MEMORY_CURATION_INSTRUCTIONS,
            }
            questions[f"{ref}_progress"] = {
                "type": "choice",
                "criteria": {
                    "POSITIVE": "The fact materially advances the objective.",
                    "NEGATIVE": "The fact shows regression or a worsening blocker.",
                    "NEUTRAL": "The fact is relevant but does not change progress.",
                    "UNCLEAR": "The progress effect cannot yet be determined.",
                },
                "instructions": MEMORY_CURATION_INSTRUCTIONS,
            }
            questions[f"{ref}_importance"] = {
                "type": "choice",
                "criteria": {
                    "HIGH": "Use importance 0.9 for a fact likely to change a future decision.",
                    "MEDIUM": "Use importance 0.5 for useful but non-critical context.",
                    "LOW": "Use importance 0.2 for limited future decision value.",
                },
                "instructions": MEMORY_CURATION_INSTRUCTIONS,
            }
            questions[f"{ref}_relation"] = {
                "type": "choice",
                "criteria": {
                    option: "Do not create a relation." if option == "NONE" else f"Create this relation from {ref}."
                    for option in _relation_options(candidate_refs, existing_targets, ref)
                },
                "instructions": MEMORY_CURATION_INSTRUCTIONS,
            }

        raw_answers = answers(await jev_call(jev, {
            **build_jev_process_state(state),
            "memory_candidates": candidates,
        }, questions))
        decisions = []
        relations = []
        for candidate in candidates:
            ref = candidate["candidate_ref"]
            importance = {"HIGH": 0.9, "MEDIUM": 0.5, "LOW": 0.2}[_choice(raw_answers.get(f"{ref}_importance"), expected={"HIGH", "MEDIUM", "LOW"})]
            answer_confidence = typed_answer(raw_answers.get(f"{ref}_keep"))["confidence"]
            decisions.append({
                "candidate_ref": ref,
                "keep": _choice(raw_answers.get(f"{ref}_keep"), expected={"KEEP", "DROP"}) == "KEEP",
                "category": _choice(raw_answers.get(f"{ref}_category"), expected={"STRATEGY", "EVIDENCE"}),
                "role": _choice(raw_answers.get(f"{ref}_role"), expected=MEMORY_TYPES),
                "status": _choice(raw_answers.get(f"{ref}_status"), expected=MEMORY_STATUSES),
                "confidence": answer_confidence,
                "progress_effect": _choice(raw_answers.get(f"{ref}_progress"), expected=PROGRESS_EFFECTS),
                "importance": importance,
            })
            relation_choice = _choice(raw_answers.get(f"{ref}_relation"))
            relation_options = _relation_options(candidate_refs, existing_targets, ref)
            selected_relation = relation_options.get(relation_choice)
            if selected_relation is not None:
                relation_type, target_ref = selected_relation
                relations.append({
                    "source_ref": ref,
                    "relation_type": relation_type,
                    "target_ref": target_ref,
                })
        scoped_ids = {
            str(_field(memory, "id"))
            for category in ("STRATEGY", "EVIDENCE")
            for memory in (state.get("process_context") or {}).get("categories", {}).get(category) or []
            if _field(memory, "id")
        }
        return {"memory_curation": validate_memory_curation({"decisions": decisions, "relations": relations}, candidates, scoped_ids)}

    return node
