from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from supervisor.agent.nodes.common import answers, jev_call, noul_value, typed_answer
from supervisor.agent.prompts import (
    CONTEXT_SUFFICIENT_INSTRUCTIONS,
    INTERVENTION_REASON_CRITERIA,
    PROGRESS_STALL_INSTRUCTIONS,
    PROCESS_OUTCOME_CRITERIA,
    PROCESS_OUTCOME_INSTRUCTIONS,
    STRATEGY_SUPPORTED_INSTRUCTIONS,
    SUPERVISION_ACTION_CRITERIA,
    SUPERVISION_ACTION_INSTRUCTIONS,
)
from supervisor.agent.schemas import REASON_CODES, RELATION_TYPES, validate_supervision_decision
from supervisor.agent.services.jev_service import JevClient
from supervisor.agent.state import SupervisorState
from supervisor.agent.state_builder import build_jev_process_state
from supervisor.memory.client import _field
from supervisor.observability import emit

logger = logging.getLogger(__name__)
ACTIONS = ["CONTINUE", "NEED_MORE_MEMORY", "INTERVENE", "CLOSE_PROCESS"]


def _intervention_evidence(state: SupervisorState, base: dict[str, Any]) -> dict[str, dict[str, Any]]:
    context = state.get("process_context") or {}
    category_id = (context.get("category_ids") or {}).get("EVIDENCE")
    evidence: dict[str, dict[str, Any]] = {}
    for item in base.get("evidence") or []:
        memory_id = item.get("id")
        if memory_id and (category_id is None or str(item.get("category_id")) == str(category_id)):
            evidence[str(memory_id)] = item
    expanded = ((base.get("expanded_memory") or {}).get("memories") or {})
    if category_id is not None:
        for item in expanded.values():
            memory_id = item.get("id")
            if memory_id and str(item.get("category_id")) == str(category_id):
                evidence.setdefault(str(memory_id), item)
    return evidence


def _newest_evidence(evidence: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    # The category search is normally newest-first, but expanded graph nodes
    # can arrive in a different order. Stable ties preserve the search order.
    items = list(evidence.items())
    def timestamp(item: dict[str, Any]) -> datetime | None:
        value = item.get("updated_at") or item.get("created_at")
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None

    items.sort(key=lambda pair: timestamp(pair[1]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return dict(items)


async def _select_intervention_evidence(
    jev: JevClient,
    state: SupervisorState,
    base: dict[str, Any],
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    evidence = _newest_evidence(_intervention_evidence(state, base))
    original_count = len(evidence)
    if not evidence:
        raise RuntimeError("INTERVENE requires at least one in-scope EVIDENCE memory")
    def questions_for(candidates: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        criteria = {
            memory_id: f"{item.get('title', 'Evidence')}: {item.get('content', '')}"[:500]
            for memory_id, item in candidates.items()
        }
        criteria["NONE"] = "Do not select another evidence memory."
        questions = {
            f"evidence_memory_{index}": {
                "type": "choice",
                "criteria": criteria,
                "instructions": "Select a distinct EVIDENCE memory that directly supports the intervention decision.",
            }
            for index in range(1, 4)
        }
        questions.update({
            f"intervention_reason_{index}": {
                "type": "choice",
                "criteria": {**INTERVENTION_REASON_CRITERIA, "NONE": "Do not select another reason."},
                "instructions": "Select a controlled explanation for the intervention decision.",
            }
            for index in range(1, 3)
        })
        return questions

    questions = questions_for(evidence)
    # A batch can be split, but one question with too many options cannot.
    # Retain the newest options, and keep the selection context in sync with
    # the choices actually offered. JevClient still guards the exact bytes of
    # every POST, including all other questions and their criteria.
    preflight = getattr(jev, "fits_single_question", None)
    if callable(preflight):
        while len(evidence) > 1:
            name = "evidence_memory_1"
            request_state = {**base, "intervention_evidence": list(evidence.values())}
            if preflight(state=request_state, name=name, question=questions[name]):
                break
            evidence.pop(next(reversed(evidence)))
            questions = questions_for(evidence)
    if len(evidence) < original_count:
        emit(logger, logging.INFO, "jev_intervention_evidence_pruned", retained_count=len(evidence),
             omitted_count=original_count - len(evidence))
    selected_answers = answers(await jev_call(
        jev,
        {**base, "intervention_evidence": list(evidence.values())},
        questions,
    ))
    evidence_ids: list[str] = []
    for index in range(1, 4):
        selected = typed_answer(selected_answers.get(f"evidence_memory_{index}"))["value"]
        if selected != "NONE":
            if selected not in evidence:
                raise ValueError("Jev selected an invalid intervention evidence memory")
            if selected not in evidence_ids:
                evidence_ids.append(selected)
    reasons: list[str] = []
    for index in range(1, 3):
        selected = typed_answer(selected_answers.get(f"intervention_reason_{index}"))["value"]
        if selected != "NONE":
            if selected not in REASON_CODES:
                raise ValueError("Jev selected an invalid intervention reason")
            if selected not in reasons:
                reasons.append(selected)
    if not evidence_ids or not reasons:
        raise ValueError("INTERVENE requires selected evidence and reason codes")
    return evidence_ids, reasons, [evidence[memory_id] for memory_id in evidence_ids]


def _expansion_targets(state: SupervisorState) -> dict[str, Any]:
    context = state.get("process_context") or {}
    categories = context.get("categories") or {}
    memory_ids = []
    for name in ("STRATEGY", "EVIDENCE"):
        for memory in categories.get(name) or []:
            memory_id = _field(memory, "id")
            if memory_id and str(memory_id) not in memory_ids:
                memory_ids.append(str(memory_id))
    related = [
        str(process_id)
        for process_id in context.get("related_process_ids") or []
        if process_id and str(process_id) != state.get("active_process_id")
    ]
    return {
        "memory_ids": memory_ids[:20],
        "relation_types": sorted(RELATION_TYPES),
        "related_process_ids": related[:10],
        "summaries": True,
    }


def make_supervision_decision(
    jev: JevClient,
    action_threshold: float | None = None,
    context_sufficient_threshold: float | None = None,
    outcome_threshold: float | None = None,
    max_expansion_depth: int = 3,
):
    # Retain the argument for existing callers. Context sufficiency remains a
    # diagnostic, never an override of Jev's selected action.
    if not 1 <= max_expansion_depth <= 3:
        raise ValueError("max_expansion_depth must be between one and three")
    action_threshold = action_threshold if action_threshold is not None else float(
        os.getenv("JEV_ACTION_MIN_CONFIDENCE", "0.6")
    )
    outcome_threshold = outcome_threshold if outcome_threshold is not None else float(
        os.getenv("JEV_OUTCOME_MIN_CONFIDENCE", str(action_threshold))
    )
    exhausted_intervention_threshold = float(
        os.getenv("JEV_EXHAUSTED_INTERVENTION_MIN_PROB", "0.75")
    )
    if not all(0 <= threshold <= 1 for threshold in (
        action_threshold,
        outcome_threshold,
        exhausted_intervention_threshold,
    )):
        raise ValueError("Jev thresholds must be between zero and one")

    async def node(state: SupervisorState):
        base = build_jev_process_state(state)
        diagnostic_questions = {
            "progress_stall_probability": {
                "type": "noul",
                "instructions": PROGRESS_STALL_INSTRUCTIONS,
            },
            "strategy_supported_probability": {
                "type": "noul",
                "instructions": STRATEGY_SUPPORTED_INSTRUCTIONS,
            },
            "context_sufficient_probability": {
                "type": "noul",
                "instructions": CONTEXT_SUFFICIENT_INSTRUCTIONS,
            },
        }
        raw_diagnostics = answers(await jev_call(jev, base, diagnostic_questions))
        diagnostics = {name: noul_value(raw_diagnostics.get(name)) for name in diagnostic_questions}
        action_answers = answers(await jev_call(
            jev,
            {**base, "diagnostics": diagnostics},
            {"action": {
                "type": "choice",
                "criteria": SUPERVISION_ACTION_CRITERIA,
                "instructions": SUPERVISION_ACTION_INSTRUCTIONS,
            }, "process_outcome": {
                "type": "choice",
                "criteria": PROCESS_OUTCOME_CRITERIA,
                "instructions": PROCESS_OUTCOME_INSTRUCTIONS,
            }},
        ))
        action_answer = typed_answer(action_answers.get("action"))
        if set(action_answer["probabilities"]) != set(ACTIONS):
            raise RuntimeError("supervision must include the complete action distribution")
        proposed = action_answer["value"]
        if proposed not in ACTIONS:
            raise ValueError("Jev selected an invalid supervision action")
        depth = state.get("memory_expansion_depth", 0)
        if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= max_expansion_depth:
            raise ValueError("invalid memory expansion depth")
        selected = proposed
        # Jev's explicit action is authoritative. Only an unresolved request
        # for more memory at the expansion limit needs a probability fallback.
        if proposed == "NEED_MORE_MEMORY" and depth >= max_expansion_depth:
            selected = (
                "INTERVENE"
                if depth >= 3
                and action_answer["confidence"] >= action_threshold
                and action_answer["probabilities"]["INTERVENE"] >= exhausted_intervention_threshold
                and bool(_intervention_evidence(state, base))
                else "CONTINUE"
            )
        outcome_answer = None
        if selected == "CLOSE_PROCESS":
            outcome_answer = typed_answer(action_answers.get("process_outcome"))
            if set(outcome_answer["probabilities"]) != {"SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"}:
                raise RuntimeError("process outcome must include the complete bounded distribution")
            if (
                outcome_answer["confidence"] < outcome_threshold
                or outcome_answer["value"] == "SUPERSEDED"
            ):
                emit(
                    logger,
                    logging.WARNING,
                    "jev_process_close_rejected",
                    process_id=state.get("active_process_id"),
                    outcome=outcome_answer["value"],
                    confidence=outcome_answer["confidence"],
                    threshold=outcome_threshold,
                )
                selected = "CONTINUE"
        intervention_evidence_ids: list[str] = []
        intervention_reason_codes: list[str] = []
        selected_intervention_memories: list[dict[str, Any]] = []
        if selected == "INTERVENE":
            (
                intervention_evidence_ids,
                intervention_reason_codes,
                selected_intervention_memories,
            ) = await _select_intervention_evidence(jev, state, base)
        decision: dict[str, Any] = {
            **diagnostics,
            "action": selected,
            "action_probabilities": action_answer["probabilities"],
            "action_confidence": action_answer["confidence"],
        }
        if selected == "NEED_MORE_MEMORY":
            decision.update(_expansion_targets(state))
        elif selected == "CLOSE_PROCESS" and outcome_answer is not None:
            decision["process_outcome"] = outcome_answer["value"]
            decision["process_outcome_probabilities"] = outcome_answer["probabilities"]
            decision["process_outcome_confidence"] = outcome_answer["confidence"]
        elif selected == "INTERVENE":
            decision["evidence_memory_ids"] = intervention_evidence_ids
            decision["reason_codes"] = intervention_reason_codes
        decision = validate_supervision_decision(decision)
        emit(
            logger,
            logging.INFO,
            "jev_supervision_decision",
            process_id=state.get("active_process_id"),
            action=decision["action"],
            diagnostics=diagnostics,
            probabilities=decision["action_probabilities"],
            confidence=decision["action_confidence"],
            process_outcome=decision.get("process_outcome"),
            process_outcome_probabilities=decision.get("process_outcome_probabilities"),
            process_outcome_confidence=decision.get("process_outcome_confidence"),
            evidence_memory_ids=decision.get("evidence_memory_ids"),
            reason_codes=decision.get("reason_codes"),
            expansion_depth=state.get("memory_expansion_depth", 0),
            proposed_action=proposed,
            decision_route="context_retrieval" if selected == "NEED_MORE_MEMORY" else "supervision",
        )
        return {
            "supervision_diagnostics": diagnostics,
            "supervision_decision": decision,
            "selected_intervention_memories": selected_intervention_memories,
        }

    return node
