"""Structured output contracts for the GRAMS Supervisor."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class ClaimedInboxEvent(TypedDict):
    """Serializable current-cycle event envelope, including lease ownership."""

    id: str
    lease_id: str
    root_session_id: str
    session_id: str | None
    type: str
    source_event: str | None
    payload: Any
    received_at: str
    source_run_id: str | None
    sequence: int | None
    ingress_id: str | None
    cycle_id: str | None


class ProcessContinuityDecision(TypedDict, total=False):
    decision: Literal["SAME_PROCESS", "NEW_PROCESS"]
    probabilities: dict[str, float]
    confidence: float


class JevAnswer(TypedDict, total=False):
    value: Any
    probabilities: dict[str, float]
    confidence: float


class SupervisionDiagnostics(TypedDict):
    progress_stall_probability: float
    strategy_supported_probability: float
    context_sufficient_probability: float


class MemoryCandidate(TypedDict, total=False):
    category: Literal["STRATEGY", "EVIDENCE"]
    title: str
    content: str
    candidate_ref: str


class RelationCandidate(TypedDict):
    source_id: str
    relation_type: str
    target_id: str


class MemoryUpdateProposal(TypedDict):
    memories: list[MemoryCandidate]
    relations: list[RelationCandidate]


class SupervisionDecision(TypedDict, total=False):
    progress_stall_probability: float
    strategy_supported_probability: float
    context_sufficient_probability: float
    action: Literal["CONTINUE", "NEED_MORE_MEMORY", "INTERVENE", "CLOSE_PROCESS"]
    memory_ids: list[str]
    relation_types: list[str]
    related_process_ids: list[str]
    summaries: bool
    process_outcome: Literal["SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"]
    process_outcome_probabilities: dict[str, float]
    process_outcome_confidence: float
    action_probabilities: dict[str, float]
    action_confidence: float


class ProcessSummary(TypedDict, total=False):
    content: str
    outcome: Literal["SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"]


RELATION_TYPES = {
    "SUPPORTS", "CONTRADICTS", "TESTED_BY", "PRODUCED", "SUCCEEDED_WITH",
    "FAILED_BECAUSE", "BLOCKED_BY", "DEPENDS_ON", "SUPERSEDES", "VALIDATES",
}
TERMINAL_OUTCOMES = {"SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"}


def validate_memory_proposal(value: Any) -> MemoryUpdateProposal:
    if not isinstance(value, dict) or not isinstance(value.get("memories"), list) or not isinstance(value.get("relations"), list):
        raise ValueError("memory update must contain memories and relations lists")
    refs: set[str] = set()
    memories = []
    for index, item in enumerate(value["memories"], start=1):
        if not isinstance(item, dict) or item.get("category") not in {"STRATEGY", "EVIDENCE"}:
            raise ValueError("memory candidates must be STRATEGY or EVIDENCE")
        if not all(isinstance(item.get(k), str) and item[k].strip() for k in ("title", "content")):
            raise ValueError("memory candidates require non-empty title and content")
        ref = item.get("candidate_ref")
        if ref != f"new_{index}" or ref in refs:
            raise ValueError("candidate_ref values must be unique sequential new_N references")
        refs.add(ref)
        memories.append({
            "category": item["category"],
            "title": item["title"].strip(),
            "content": item["content"].strip(),
            "candidate_ref": ref,
        })
    for item in value["relations"]:
        if not isinstance(item, dict) or item.get("relation_type") not in RELATION_TYPES:
            raise ValueError("relation type is not supported")
        if not all(isinstance(item.get(k), str) and item[k].strip() for k in ("source_id", "target_id")):
            raise ValueError("relations require non-empty IDs")
        for endpoint in (item["source_id"], item["target_id"]):
            if endpoint.startswith("new_") and endpoint not in refs:
                raise ValueError(f"relation references unknown candidate {endpoint}")
        if item["source_id"] == item["target_id"]:
            raise ValueError("self-relations are not supported")
    relations = [dict(relation) for relation in value["relations"]]
    identities = {(r["source_id"], r["relation_type"], r["target_id"]) for r in relations}
    if len(identities) != len(relations):
        raise ValueError("duplicate relation candidates are not supported")
    return {"memories": memories, "relations": relations}


def validate_supervision_decision(value: Any) -> SupervisionDecision:
    if not isinstance(value, dict) or value.get("action") not in {"CONTINUE", "NEED_MORE_MEMORY", "INTERVENE", "CLOSE_PROCESS"}:
        raise ValueError("review returned an invalid action")
    for key in ("memory_ids", "relation_types", "related_process_ids"):
        if key in value and (not isinstance(value[key], list) or any(not isinstance(x, str) or not x.strip() for x in value[key])):
            raise ValueError(f"review field {key} is invalid")
    action = value["action"]
    if "relation_types" in value and any(item not in RELATION_TYPES for item in value["relation_types"]):
        raise ValueError("review requested an unsupported relation type")
    if "summaries" in value and not isinstance(value["summaries"], bool):
        raise ValueError("review field summaries is invalid")
    if action == "NEED_MORE_MEMORY" and not (
        any(value.get(k) for k in ("memory_ids", "relation_types", "related_process_ids"))
        or value.get("summaries") is True
    ):
        raise ValueError("NEED_MORE_MEMORY requires a memory request")
    if action == "CLOSE_PROCESS" and value.get("process_outcome") not in TERMINAL_OUTCOMES:
        raise ValueError("CLOSE_PROCESS requires a terminal outcome")
    if action == "CLOSE_PROCESS":
        outcome_probabilities = value.get("process_outcome_probabilities")
        if not isinstance(outcome_probabilities, dict) or set(outcome_probabilities) != TERMINAL_OUTCOMES:
            raise ValueError("CLOSE_PROCESS requires the complete outcome distribution")
        if any(
            not isinstance(item, (int, float)) or not 0 <= float(item) <= 1
            for item in outcome_probabilities.values()
        ):
            raise ValueError("process outcome probabilities are invalid")
        outcome_confidence = value.get("process_outcome_confidence")
        if not isinstance(outcome_confidence, (int, float)) or not 0 <= float(outcome_confidence) <= 1:
            raise ValueError("process outcome confidence is invalid")
    for key in ("progress_stall_probability", "strategy_supported_probability", "context_sufficient_probability"):
        if not isinstance(value.get(key), (int, float)) or not 0 <= float(value[key]) <= 1:
            raise ValueError(f"supervision field {key} is invalid")
    probabilities = value.get("action_probabilities")
    actions = {"CONTINUE", "NEED_MORE_MEMORY", "INTERVENE", "CLOSE_PROCESS"}
    if not isinstance(probabilities, dict) or set(probabilities) != actions:
        raise ValueError("supervision decision requires the complete action distribution")
    if any(not isinstance(item, (int, float)) or not 0 <= float(item) <= 1 for item in probabilities.values()):
        raise ValueError("supervision action probabilities are invalid")
    confidence = value.get("action_confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise ValueError("supervision action confidence is invalid")
    return dict(value)


def validate_summary(value: Any) -> ProcessSummary:
    if not isinstance(value, dict) or not isinstance(value.get("content"), str) or not value["content"].strip() or value.get("outcome") not in TERMINAL_OUTCOMES:
        raise ValueError("summary requires non-empty content and terminal outcome")
    return {"content": value["content"].strip(), "outcome": value["outcome"]}


def validate_continuity(value: Any) -> ProcessContinuityDecision:
    if not isinstance(value, dict) or value.get("decision") not in {"SAME_PROCESS", "NEW_PROCESS"}:
        raise ValueError("continuity returned an invalid decision")
    return dict(value)


MEMORY_UPDATE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["memories", "relations"],
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["category", "title", "content", "candidate_ref"],
                "properties": {
                    "category": {"type": "string", "enum": ["STRATEGY", "EVIDENCE"]},
                    "title": {"type": "string", "minLength": 1},
                    "content": {"type": "string", "minLength": 1},
                    "candidate_ref": {"type": "string", "pattern": "^new_[1-9][0-9]*$"},
                },
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_id", "relation_type", "target_id"],
                "properties": {
                    "source_id": {"type": "string", "minLength": 1},
                    "relation_type": {"type": "string", "enum": sorted(RELATION_TYPES)},
                    "target_id": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}

PROCESS_SUMMARY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["content"],
    "properties": {"content": {"type": "string", "minLength": 1}},
}
