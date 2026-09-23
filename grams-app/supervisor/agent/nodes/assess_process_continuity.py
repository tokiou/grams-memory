from __future__ import annotations

import logging
import os

from .common import answers, jev_call, typed_answer
from supervisor.agent.services.jev_service import JevClient
from supervisor.agent.prompts import PROCESS_CONTINUITY_CRITERIA, PROCESS_CONTINUITY_INSTRUCTIONS
from supervisor.agent.schemas import validate_continuity
from supervisor.agent.state import SupervisorState
from supervisor.agent.state_builder import build_jev_process_state
from supervisor.observability import emit

logger = logging.getLogger(__name__)

def make_assess_process_continuity(jev: JevClient, new_process_threshold: float | None = None):
    threshold = new_process_threshold if new_process_threshold is not None else float(
        os.getenv("JEV_NEW_PROCESS_MIN_PROB", "0.7")
    )
    if not 0 <= threshold <= 1:
        raise ValueError("new process threshold must be between zero and one")

    async def node(state: SupervisorState):
        result = await jev_call(jev, build_jev_process_state(state), {
            "continuity": {
                "type": "choice",
                "criteria": PROCESS_CONTINUITY_CRITERIA,
                "instructions": PROCESS_CONTINUITY_INSTRUCTIONS,
            },
        })
        answer = typed_answer(answers(result).get("continuity"))
        if set(answer["probabilities"]) != {"SAME_PROCESS", "NEW_PROCESS"}:
            raise RuntimeError("continuity must include the complete bounded distribution")
        answer["value"] = "NEW_PROCESS" if answer["probabilities"]["NEW_PROCESS"] >= threshold else "SAME_PROCESS"
        decision = validate_continuity({
            "decision": answer["value"],
            "probabilities": answer["probabilities"],
            "confidence": answer["confidence"],
        })
        update = {"process_continuity": decision}
        if decision["decision"] == "NEW_PROCESS":
            update["pending_process_transition"] = {"predecessor_id": state["active_process_id"]}
        emit(logger, logging.INFO, "jev_continuity_decision",
             process_id=state.get("active_process_id"), action=decision["decision"],
             probabilities=decision["probabilities"], confidence=decision["confidence"])
        return update
    return node
