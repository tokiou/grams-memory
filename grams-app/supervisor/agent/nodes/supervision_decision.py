from __future__ import annotations

import logging
import os
from typing import Any

from supervisor.agent.nodes.common import answers, jev_call, noul_value, typed_answer
from supervisor.agent.prompts import (
    CONTEXT_SUFFICIENT_INSTRUCTIONS,
    PROGRESS_STALL_INSTRUCTIONS,
    PROCESS_OUTCOME_CRITERIA,
    PROCESS_OUTCOME_INSTRUCTIONS,
    STRATEGY_SUPPORTED_INSTRUCTIONS,
    SUPERVISION_ACTION_CRITERIA,
    SUPERVISION_ACTION_INSTRUCTIONS,
)
from supervisor.agent.schemas import RELATION_TYPES, validate_supervision_decision
from supervisor.agent.state_builder import build_jev_process_state
from supervisor.memory.client import _field
from supervisor.observability import emit

logger = logging.getLogger(__name__)
ACTIONS = ["CONTINUE", "NEED_MORE_MEMORY", "INTERVENE", "CLOSE_PROCESS"]


def _expansion_targets(state: dict[str, Any]) -> dict[str, Any]:
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
    jev,
    action_threshold: float | None = None,
    context_sufficient_threshold: float | None = None,
    outcome_threshold: float | None = None,
):
    action_threshold = action_threshold if action_threshold is not None else float(
        os.getenv("JEV_ACTION_MIN_CONFIDENCE", "0.6")
    )
    context_sufficient_threshold = (
        context_sufficient_threshold
        if context_sufficient_threshold is not None
        else float(os.getenv("JEV_CONTEXT_SUFFICIENT_MIN_PROB", "0.6"))
    )
    outcome_threshold = outcome_threshold if outcome_threshold is not None else float(
        os.getenv("JEV_OUTCOME_MIN_CONFIDENCE", str(action_threshold))
    )
    exhausted_intervention_threshold = float(
        os.getenv("JEV_EXHAUSTED_INTERVENTION_MIN_PROB", "0.75")
    )
    if not all(0 <= threshold <= 1 for threshold in (
        action_threshold,
        context_sufficient_threshold,
        outcome_threshold,
        exhausted_intervention_threshold,
    )):
        raise ValueError("Jev thresholds must be between zero and one")

    async def node(state):
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
        selected = action_answer["value"]
        if diagnostics["context_sufficient_probability"] < context_sufficient_threshold:
            selected = "NEED_MORE_MEMORY"
        elif action_answer["confidence"] < action_threshold:
            selected = "CONTINUE"
        if (
            selected == "NEED_MORE_MEMORY"
            and int(state.get("memory_expansion_depth", 0)) >= 2
            and action_answer["probabilities"]["INTERVENE"] > exhausted_intervention_threshold
        ):
            selected = "INTERVENE"
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
            expansion_depth=state.get("memory_expansion_depth", 0),
        )
        return {"supervision_diagnostics": diagnostics, "supervision_decision": decision}

    return node
