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


class CandidateEvidence(TypedDict):
    event_id: str
    excerpt: str


class CandidateProvenance(TypedDict, total=False):
    source_event_ids: list[str]
    source_event_types: list[str]
    cycle_id: str | None


class FactualMemoryCandidate(TypedDict):
    candidate_ref: str
    fact: str
    evidence: list[CandidateEvidence]
    provenance: CandidateProvenance


class MemoryCurationRelation(TypedDict, total=False):
    source_ref: str
    relation_type: str
    target_ref: str
    confidence: float
    evidence_strength: Literal["WEAK", "MEDIUM", "STRONG"]
    direct: bool


class MemoryCurationDecision(TypedDict):
    candidate_ref: str
    keep: bool
    category: Literal["STRATEGY", "EVIDENCE"]
    role: str
    status: str
    confidence: float
    progress_effect: Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "UNCLEAR"]
    importance: float


class MemoryCurationProposal(TypedDict):
    decisions: list[MemoryCurationDecision]
    relations: list[MemoryCurationRelation]


class MemoryMaterialization(TypedDict):
    candidate_ref: str
    title: str
    content: str


class MemoryCandidate(TypedDict, total=False):
    category: Literal["STRATEGY", "EVIDENCE"]
    title: str
    content: str
    candidate_ref: str
    role: str
    status: str
    confidence: float
    progress_effect: Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "UNCLEAR"]
    importance: float
    provenance: CandidateProvenance


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
    evidence_memory_ids: list[str]
    reason_codes: list[str]


class ProcessSummary(TypedDict, total=False):
    content: str
    outcome: Literal["SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"]


RELATION_TYPES = {
    "SUPPORTS", "CONTRADICTS", "TESTED_BY", "PRODUCED", "SUCCEEDED_WITH",
    "FAILED_BECAUSE", "BLOCKED_BY", "DEPENDS_ON", "SUPERSEDES", "VALIDATES",
}
MEMORY_TYPES = {
    "FACT", "OBSERVATION", "HYPOTHESIS", "CONSTRAINT", "ACTION", "ERROR",
    "DECISION", "STATE", "RESULT",
}
MEMORY_STATUSES = {
    "ACTIVE", "TENTATIVE", "CONFIRMED", "REJECTED", "SUPERSEDED", "RESOLVED",
    "FAILED", "BLOCKED", "VALIDATED",
}
PROGRESS_EFFECTS = {"POSITIVE", "NEGATIVE", "NEUTRAL", "UNCLEAR"}
EVIDENCE_STRENGTHS = {"WEAK", "MEDIUM", "STRONG"}
REASON_CODES = {
    "POSSIBLE_PROGRESS_STALL",
    "POSSIBLE_RESEARCH_LOOP",
    "POSSIBLE_HYPOTHESIS_OSCILLATION",
    "DELIVERABLE_MISSING",
    "VALIDATION_MISSING",
    "REPEATED_FAILURE",
    "OBJECTIVE_DRIFT",
}
TERMINAL_OUTCOMES = {"SUCCEEDED", "FAILED", "SUPERSEDED", "ABANDONED"}
MAX_MEMORY_CANDIDATES = 6
MAX_MEMORY_TITLE_LENGTH = 200
MAX_MEMORY_CONTENT_LENGTH = 1200
MAX_RELATION_CANDIDATES = 12
MAX_SUMMARY_LENGTH = 4000
MAX_CANDIDATE_FACT_LENGTH = 1200
MAX_CANDIDATE_EVIDENCE_LENGTH = 1000
MAX_CANDIDATE_EVIDENCE_PER_MEMORY = 4
CURATION_CONTRACT_VERSION = 1


def validate_memory_proposal(value: Any) -> MemoryUpdateProposal:
    if not isinstance(value, dict) or not isinstance(value.get("memories"), list) or not isinstance(value.get("relations"), list):
        raise ValueError("memory update must contain memories and relations lists")
    if len(value["memories"]) > MAX_MEMORY_CANDIDATES:
        raise ValueError(f"memory update cannot contain more than {MAX_MEMORY_CANDIDATES} memories")
    if len(value["relations"]) > MAX_RELATION_CANDIDATES:
        raise ValueError(f"memory update cannot contain more than {MAX_RELATION_CANDIDATES} relations")
    refs: set[str] = set()
    memories = []
    for index, item in enumerate(value["memories"], start=1):
        if not isinstance(item, dict) or item.get("category") not in {"STRATEGY", "EVIDENCE"}:
            raise ValueError("memory candidates must be STRATEGY or EVIDENCE")
        if not all(isinstance(item.get(k), str) and item[k].strip() for k in ("title", "content")):
            raise ValueError("memory candidates require non-empty title and content")
        if len(item["title"].strip()) > MAX_MEMORY_TITLE_LENGTH:
            raise ValueError(f"memory candidate titles cannot exceed {MAX_MEMORY_TITLE_LENGTH} characters")
        if len(item["content"].strip()) > MAX_MEMORY_CONTENT_LENGTH:
            raise ValueError(f"memory candidate content cannot exceed {MAX_MEMORY_CONTENT_LENGTH} characters")
        ref = item.get("candidate_ref")
        if ref != f"new_{index}" or ref in refs:
            raise ValueError("candidate_ref values must be unique sequential new_N references")
        refs.add(ref)
        normalized = {
            "category": item["category"],
            "title": item["title"].strip(),
            "content": item["content"].strip(),
            "candidate_ref": ref,
        }
        for key in ("role", "status", "progress_effect", "provenance"):
            if key in item:
                normalized[key] = item[key]
        if "role" in item and item["role"] not in MEMORY_TYPES:
            raise ValueError("memory candidate role is invalid")
        if "status" in item and item["status"] not in MEMORY_STATUSES:
            raise ValueError("memory candidate status is invalid")
        if "progress_effect" in item and item["progress_effect"] not in PROGRESS_EFFECTS:
            raise ValueError("memory candidate progress effect is invalid")
        if "confidence" in item:
            if not isinstance(item["confidence"], (int, float)) or not 0 <= float(item["confidence"]) <= 1:
                raise ValueError("memory candidate confidence is invalid")
            normalized["confidence"] = float(item["confidence"])
        if "importance" in item:
            if not isinstance(item["importance"], (int, float)) or not 0 <= float(item["importance"]) <= 1:
                raise ValueError("memory candidate importance is invalid")
            normalized["importance"] = float(item["importance"])
        memories.append(normalized)
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


def validate_memory_candidates(value: Any, claimed_events: list[dict[str, Any]]) -> list[FactualMemoryCandidate]:
    """Validate factual candidates before they enter the curation stage."""
    if not isinstance(value, dict) or not isinstance(value.get("candidates"), list):
        raise ValueError("memory candidates must contain a candidates list")
    if len(value["candidates"]) > MAX_MEMORY_CANDIDATES:
        raise ValueError(f"memory candidates cannot contain more than {MAX_MEMORY_CANDIDATES} candidates")
    event_ids = {str(event.get("id")) for event in claimed_events if isinstance(event, dict) and event.get("id")}
    event_types = {
        str(event.get("id")): str(event.get("type"))
        for event in claimed_events
        if isinstance(event, dict) and event.get("id")
    }
    refs: set[str] = set()
    candidates: list[FactualMemoryCandidate] = []
    for index, item in enumerate(value["candidates"], start=1):
        if not isinstance(item, dict) or item.get("candidate_ref") != f"new_{index}":
            raise ValueError("candidate_ref values must be unique sequential new_N references")
        if item["candidate_ref"] in refs:
            raise ValueError("candidate_ref values must be unique")
        fact = item.get("fact")
        if not isinstance(fact, str) or not fact.strip() or len(fact.strip()) > MAX_CANDIDATE_FACT_LENGTH:
            raise ValueError("factual candidates require bounded non-empty facts")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence or len(evidence) > MAX_CANDIDATE_EVIDENCE_PER_MEMORY:
            raise ValueError("each candidate requires one to four evidence items")
        normalized_evidence: list[CandidateEvidence] = []
        for proof in evidence:
            if not isinstance(proof, dict) or proof.get("event_id") not in event_ids:
                raise ValueError("candidate evidence must reference a claimed event")
            excerpt = proof.get("excerpt")
            if not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt.strip()) > MAX_CANDIDATE_EVIDENCE_LENGTH:
                raise ValueError("candidate evidence excerpts are invalid")
            normalized_evidence.append({"event_id": proof["event_id"], "excerpt": excerpt.strip()})
        provenance = item.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError("candidate provenance is required")
        source_event_ids = provenance.get("source_event_ids")
        if (
            not isinstance(source_event_ids, list)
            or not source_event_ids
            or any(event_id not in event_ids for event_id in source_event_ids)
        ):
            raise ValueError("candidate provenance must reference claimed events")
        source_event_types = provenance.get("source_event_types")
        if (
            not isinstance(source_event_types, list)
            or not source_event_types
            or any(not isinstance(event_type, str) or not event_type.strip() for event_type in source_event_types)
        ):
            raise ValueError("candidate provenance requires source event types")
        if set(source_event_types) - set(event_types[event_id] for event_id in source_event_ids):
            raise ValueError("candidate provenance contains an unrelated event type")
        refs.add(item["candidate_ref"])
        candidates.append({
            "candidate_ref": item["candidate_ref"],
            "fact": fact.strip(),
            "evidence": normalized_evidence,
            "provenance": {
                "source_event_ids": list(dict.fromkeys(source_event_ids)),
                "source_event_types": list(dict.fromkeys(source_event_types)),
                "cycle_id": provenance.get("cycle_id"),
            },
        })
    return candidates


def validate_memory_curation(
    value: Any,
    candidates: list[FactualMemoryCandidate],
    scoped_memory_ids: set[str] | None = None,
) -> MemoryCurationProposal:
    if not isinstance(value, dict) or not isinstance(value.get("decisions"), list) or not isinstance(value.get("relations"), list):
        raise ValueError("memory curation must contain decisions and relations lists")
    if len(value["decisions"]) != len(candidates):
        raise ValueError("memory curation requires exactly one decision per candidate")
    refs = {candidate["candidate_ref"] for candidate in candidates}
    decisions: list[MemoryCurationDecision] = []
    for item in value["decisions"]:
        if not isinstance(item, dict) or item.get("candidate_ref") not in refs:
            raise ValueError("curation decision references an unknown candidate")
        if item["category"] not in {"STRATEGY", "EVIDENCE"}:
            raise ValueError("curation category is invalid")
        if not isinstance(item.get("keep"), bool):
            raise ValueError("curation keep must be boolean")
        if item.get("role") not in MEMORY_TYPES or item.get("status") not in MEMORY_STATUSES:
            raise ValueError("curation role or status is invalid")
        if item.get("progress_effect") not in PROGRESS_EFFECTS:
            raise ValueError("curation progress effect is invalid")
        for key in ("confidence", "importance"):
            if not isinstance(item.get(key), (int, float)) or not 0 <= float(item[key]) <= 1:
                raise ValueError(f"curation {key} is invalid")
        decisions.append({
            "candidate_ref": item["candidate_ref"],
            "keep": item["keep"],
            "category": item["category"],
            "role": item["role"],
            "status": item["status"],
            "confidence": float(item["confidence"]),
            "progress_effect": item["progress_effect"],
            "importance": float(item["importance"]),
        })
    if {item["candidate_ref"] for item in decisions} != refs:
        raise ValueError("curation decisions must cover every candidate exactly once")
    relations: list[MemoryCurationRelation] = []
    scoped_memory_ids = scoped_memory_ids or set()
    identities: set[tuple[str, str, str]] = set()
    for item in value["relations"]:
        if not isinstance(item, dict) or item.get("relation_type") not in RELATION_TYPES:
            raise ValueError("curation relation type is invalid")
        source = item.get("source_ref")
        target = item.get("target_ref")
        if not isinstance(source, str) or not isinstance(target, str) or not source.strip() or not target.strip():
            raise ValueError("curation relations require source_ref and target_ref")
        if source == target or (source not in refs and source not in scoped_memory_ids) or (target not in refs and target not in scoped_memory_ids):
            raise ValueError("curation relation references an invalid endpoint")
        kept_refs = {item["candidate_ref"] for item in decisions if item["keep"]}
        if source in refs and source not in kept_refs or target in refs and target not in kept_refs:
            raise ValueError("curation relation references a discarded candidate")
        identity = (source, item["relation_type"], target)
        if identity in identities:
            raise ValueError("duplicate curation relations are not supported")
        identities.add(identity)
        relation = {"source_ref": source, "relation_type": item["relation_type"], "target_ref": target}
        if "confidence" in item:
            if not isinstance(item["confidence"], (int, float)) or not 0 <= float(item["confidence"]) <= 1:
                raise ValueError("curation relation confidence is invalid")
            relation["confidence"] = float(item["confidence"])
        if "evidence_strength" in item:
            if item["evidence_strength"] not in EVIDENCE_STRENGTHS:
                raise ValueError("curation relation evidence strength is invalid")
            relation["evidence_strength"] = item["evidence_strength"]
        if "direct" in item:
            if not isinstance(item["direct"], bool):
                raise ValueError("curation relation direct is invalid")
            relation["direct"] = item["direct"]
        relations.append(relation)
    if len(relations) > MAX_RELATION_CANDIDATES:
        raise ValueError(f"memory curation cannot contain more than {MAX_RELATION_CANDIDATES} relations")
    return {"decisions": decisions, "relations": relations}


def validate_materializations(value: Any, kept_refs: set[str]) -> list[MemoryMaterialization]:
    if not isinstance(value, dict) or not isinstance(value.get("memories"), list):
        raise ValueError("memory materialization must contain a memories list")
    if len(value["memories"]) != len(kept_refs):
        raise ValueError("memory materialization must cover every kept candidate")
    seen: set[str] = set()
    materializations: list[MemoryMaterialization] = []
    for item in value["memories"]:
        if not isinstance(item, dict) or item.get("candidate_ref") not in kept_refs:
            raise ValueError("materialization references a candidate that was not kept")
        ref = item["candidate_ref"]
        if ref in seen:
            raise ValueError("materialization candidate references must be unique")
        if not all(isinstance(item.get(key), str) and item[key].strip() for key in ("title", "content")):
            raise ValueError("materializations require non-empty title and content")
        if len(item["title"].strip()) > MAX_MEMORY_TITLE_LENGTH or len(item["content"].strip()) > MAX_MEMORY_CONTENT_LENGTH:
            raise ValueError("materialization text exceeds the memory bounds")
        seen.add(ref)
        materializations.append({"candidate_ref": ref, "title": item["title"].strip(), "content": item["content"].strip()})
    if seen != kept_refs:
        raise ValueError("materialization must contain exactly the kept candidates")
    return materializations


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
    if len(value["content"].strip()) > MAX_SUMMARY_LENGTH:
        raise ValueError(f"summary content cannot exceed {MAX_SUMMARY_LENGTH} characters")
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

MEMORY_CANDIDATES_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": MAX_MEMORY_CANDIDATES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["candidate_ref", "fact", "evidence", "provenance"],
                "properties": {
                    "candidate_ref": {"type": "string", "pattern": "^new_[1-9][0-9]*$"},
                    "fact": {"type": "string", "minLength": 1, "maxLength": MAX_CANDIDATE_FACT_LENGTH},
                    "evidence": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_CANDIDATE_EVIDENCE_PER_MEMORY,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["event_id", "excerpt"],
                            "properties": {
                                "event_id": {"type": "string", "minLength": 1},
                                "excerpt": {"type": "string", "minLength": 1, "maxLength": MAX_CANDIDATE_EVIDENCE_LENGTH},
                            },
                        },
                    },
                    "provenance": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["source_event_ids", "source_event_types", "cycle_id"],
                        "properties": {
                            "source_event_ids": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                            "source_event_types": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                            "cycle_id": {"type": ["string", "null"]},
                        },
                    },
                },
            },
        },
    },
}

MEMORY_MATERIALIZATION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["memories"],
    "properties": {
        "memories": {
            "type": "array",
            "maxItems": MAX_MEMORY_CANDIDATES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["candidate_ref", "title", "content"],
                "properties": {
                    "candidate_ref": {"type": "string", "pattern": "^new_[1-9][0-9]*$"},
                    "title": {"type": "string", "minLength": 1, "maxLength": MAX_MEMORY_TITLE_LENGTH},
                    "content": {"type": "string", "minLength": 1, "maxLength": MAX_MEMORY_CONTENT_LENGTH},
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
