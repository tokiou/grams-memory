from __future__ import annotations

import hashlib
from typing import Any

from supervisor.agent.services.jev_service import JevClient
from supervisor.agent.state import SupervisorState


async def jev_call(
    client: JevClient,
    state: dict[str, Any],
    questions: dict[str, Any],
) -> dict[str, Any]:
    if not hasattr(client, "system_one"):
        raise TypeError("Jev client must provide system_one")
    result = await client.system_one(state=state, questions=questions)
    if not isinstance(result, dict):
        raise RuntimeError("Jev returned an invalid response")
    return result


def cycle_key(state: SupervisorState) -> str:
    events = [event for event in state.get("claimed_events") or [] if isinstance(event, dict)]
    cycle_ids = {str(event["cycle_id"]) for event in events if event.get("cycle_id")}
    if cycle_ids:
        if len(cycle_ids) != 1:
            raise ValueError("claimed events must belong to one durable cycle")
        return next(iter(cycle_ids))
    event_ids = [str(event.get("id")) for event in events if event.get("id")]
    if not event_ids:
        raise ValueError("claimed event IDs are required for a durable cycle key")
    # The oldest claimed event remains stable when later events join a retry batch.
    digest = hashlib.sha256(event_ids[0].encode("utf-8")).hexdigest()[:20]
    return f"cycle-{digest}"


def answers(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("answers", result)
    if not isinstance(value, dict):
        raise RuntimeError("Jev returned invalid answers")
    return value


def answer_value(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("value", "choice", "decision"):
            if key in value:
                return value[key]
    return value


def typed_answer(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") != "choice":
        raise RuntimeError("Jev answer must be typed")
    answer = answer_value(value)
    probabilities = value.get("probabilities")
    confidence = value.get("confidence")
    if not isinstance(probabilities, dict) or any(
        not isinstance(key, str) or not isinstance(probability, (int, float))
        for key, probability in probabilities.items()
    ):
        raise RuntimeError("Jev answer must include a probability distribution")
    if not isinstance(confidence, (int, float)):
        raise RuntimeError("Jev answer must include confidence")
    if any(not 0 <= float(probability) <= 1 for probability in probabilities.values()):
        raise RuntimeError("Jev probabilities must be between zero and one")
    if abs(sum(float(probability) for probability in probabilities.values()) - 1.0) > 0.01:
        raise RuntimeError("Jev probabilities must form a complete distribution")
    if not 0 <= float(confidence) <= 1:
        raise RuntimeError("Jev confidence must be between zero and one")
    return {
        "value": answer,
        "probabilities": {key: float(probability) for key, probability in probabilities.items()},
        "confidence": float(confidence),
    }


def noul_value(value: Any) -> float:
    if (
        not isinstance(value, dict)
        or value.get("type") != "noul"
        or not isinstance(value.get("noul"), (int, float))
    ):
        raise RuntimeError("Jev Noul answer must include noul")
    result = float(value["noul"])
    if not 0 <= result <= 1:
        raise RuntimeError("Jev Noul answer must be between zero and one")
    return result
