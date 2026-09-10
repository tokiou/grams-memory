"""Preparation and deterministic policy for the REVIEW node."""

from datetime import datetime, timezone
from typing import Any
import json
import logging

import httpx

from ...inbox import EventInbox
from ...observability import emit
from ...opencode import OpenCodeClient
from ...memory.manifest import resolve_category, scope_for_category, validate_scope
from ..prompts import build_review_context
from ..state import SupervisorState
from ..nodes.memory_operation import OPERATIONS
from .service import ReviewService, parse_timestamp
from .schema import PreparedReview, ReviewDecision, ReviewModel, ReviewMemoryContext, VALID_ACTIONS

logger = logging.getLogger(__name__)


def fallback_action(state: SupervisorState) -> ReviewDecision:
    if state.get("claimed_events"):
        return ReviewDecision("READ_INBOX", "claimed events must be incorporated", "fallback")
    current = state.get("current_events", [])
    if state.get("pending_intervention"):
        return ReviewDecision("INTERVENE", "pending intervention", "fallback")
    if current and not state.get("memory_operation_result"):
        query = state.get("original_task") or state.get("current_task") or "supervisor operational context"
        return ReviewDecision("MEMORY_OPERATION", "operational state needs memory context", "fallback",
                              memory_operation={"operation": "search", "arguments": {"query": query}})
    if current and state.get("memory_operation_result", {}).get("operation") == "search":
        observation = {
            "root_session_id": state.get("root_session_id"), "activity": state.get("current_activity"),
            "task": state.get("current_task"),
            "events": [{"id": event.get("id"), "type": event.get("type")} for event in current[-10:]],
        }
        return ReviewDecision("MEMORY_OPERATION", "new observations need persistence", "fallback", memory_operation={
            "operation": "create",
            "arguments": {"content": json.dumps(observation, ensure_ascii=False),
                          "title": "Supervisor trajectory observation", "type": "OBSERVATION"},
        })
    return ReviewDecision("DONE", "no useful work", "fallback")


async def prepare_review(
    inbox: EventInbox, opencode: OpenCodeClient, service: ReviewService, state: SupervisorState,
    *, memory_manifest: dict[str, Any] | None, memory_category_id: str | None,
) -> PreparedReview:
    evaluation_timestamp = datetime.now(timezone.utc)
    tick = bool(state.get("supervisor_tick"))
    pending_count = await inbox.pending_count(state.get("root_session_id"))
    context = build_review_context(state, pending_count)
    manifest = state.get("memory_manifest") or memory_manifest or {"projects": []}
    memory = await prepare_memory_context(service, state, manifest, memory_category_id,
                                          state.get("root_session_id"), tick=tick)
    progress = service.progress(state, evaluation_timestamp, tick=tick)
    opencode_context: dict[str, Any] = {}
    root_session_id = state.get("root_session_id")
    has_pending_link = (isinstance(state.get("memory_operation"), dict)
                        and state["memory_operation"].get("operation") == "link")
    if has_pending_link:
        opencode_context = state.get("opencode_context") or {}
    if root_session_id and root_session_id != "default" and not state.get("claimed_events") and not has_pending_link:
        try:
            opencode_context = await opencode.get_context(root_session_id)
        except httpx.HTTPError as error:
            emit(logger, logging.WARNING, "opencode_context_unavailable", service="opencode",
                 root_session_id=root_session_id, error=type(error).__name__)
            opencode_context = {"unavailable": True}
    context.update({"memory_manifest": memory.manifest, "retrieved_memories": memory.retrieved_memories,
                    "evaluation_timestamp": evaluation_timestamp.isoformat(), "progress": progress,
                    "memory_read_status": memory.read_status, "memory_read_error": memory.read_error,
                    "opencode_context": opencode_context})
    return PreparedReview(evaluation_timestamp, tick, pending_count, context, progress, memory, opencode_context)


async def prepare_memory_context(
    service: ReviewService, state: SupervisorState, manifest: dict[str, Any], memory_category_id: str | None,
    root_session_id: str | None, *, tick: bool,
) -> ReviewMemoryContext:
    session_category_id = memory_category_id
    session_scoped = bool(root_session_id and root_session_id != "default")
    if session_scoped:
        session_category_id = None
        if service.memory is not None:
            session_category_id = await service.memory.ensure_session_hierarchy(root_session_id)
    if service.memory is not None:
        live_manifest = await service.memory.get_manifest()
        manifest = live_manifest if isinstance(live_manifest, dict) else manifest
    if session_scoped:
        projects = [project for project in manifest.get("projects", []) if project.get("name") == root_session_id]
        manifest = {"projects": projects}
        if not projects or not any(category.get("id") == session_category_id
                                   for key in projects[0].get("keys", [])
                                   for category in key.get("categories", [])):
            session_category_id = None
    status = state.get("memory_read_status", "NOT_STARTED")
    query = state.get("memory_read_query")
    error = state.get("memory_read_error")
    memories = list(state.get("retrieved_memories", []))
    if session_scoped and not state.get("claimed_events"):
        status, query, error, memories = await service.read_session_memories(state, manifest, root_session_id, tick=tick)
    return ReviewMemoryContext(manifest, session_category_id, status, query, error, memories)


def deterministic_decision(state: SupervisorState) -> ReviewDecision | None:
    if state.get("claimed_events"):
        return ReviewDecision("READ_INBOX", "claimed events must be incorporated before other work", "deterministic",
                              pending_intervention=state.get("pending_intervention"))
    pending_operation = state.get("memory_operation")
    if isinstance(pending_operation, dict) and pending_operation.get("operation") == "link":
        return ReviewDecision("MEMORY_OPERATION", "a created memory has a pending relationship to persist", "deterministic",
                              memory_operation=pending_operation, memory_link_request=state.get("memory_link_request"),
                              pending_intervention=state.get("pending_intervention"))
    return None


def normalize_model_decision(raw: dict[str, Any], prepared: PreparedReview) -> ReviewDecision:
    action = str(raw.get("action", "DONE")).upper()
    decision = ReviewDecision(action, str(raw.get("reason", "model decision")), "model")
    operation = str(raw.get("operation", "")).lower()
    if action == "MEMORY_OPERATION":
        if operation not in OPERATIONS:
            return ReviewDecision("DONE", "missing or invalid memory operation", "model")
        arguments = dict(raw.get("arguments") or {})
        embedded_link = arguments.pop("link", None)
        validate_scope(prepared.memory.manifest, arguments)
        if operation in {"create", "update"}:
            category = resolve_category(prepared.memory.manifest, arguments, prepared.memory.session_category_id)
            if category:
                for field in ("project_id", "key_id"):
                    if category.get(field):
                        arguments[field] = category[field]
                arguments["category_id"] = category["id"]
        elif operation == "search" and not any(arguments.get(key) for key in ("category_id", "key_id", "project_id")):
            arguments.update(scope_for_category(prepared.memory.manifest, prepared.memory.session_category_id))
        link = raw.get("link") if isinstance(raw.get("link"), dict) else embedded_link
        decision.memory_operation = {"operation": operation, "arguments": arguments}
        if isinstance(link, dict):
            decision.memory_link_request = dict(link)
    elif action == "INTERVENE":
        decision.pending_intervention = dict(raw.get("intervention") or raw.get("arguments") or {})
    return decision


async def resolve_decision(state: SupervisorState, prepared: PreparedReview, model: ReviewModel | None) -> ReviewDecision:
    fallback = fallback_action(state)
    if prepared.memory.read_status == "FAILED":
        already_notified = bool(state.get("last_intervention_at"))
        return ReviewDecision("DONE" if already_notified else "INTERVENE",
                              "Memory context is unavailable; ending this review after notifying the action agent"
                              if already_notified else "Memory context is unavailable; the Supervisor will retry on the next event",
                              "deterministic", pending_intervention=None if already_notified else {
                                  "action": "message", "message": "Supervisor memory is temporarily unavailable. Continue only with a safe, evidence-based step while memory access is retried.",
                              })
    if model is None:
        return fallback
    try:
        raw = await model.decide(prepared.context)
    except Exception as error:
        emit(logger, logging.WARNING, "review_fallback", service="model", error=type(error).__name__,
             detail=str(error), root_session_id=state.get("root_session_id"), run_id=state.get("run_id"),
             supervisor_tick=prepared.tick, decision_source="fallback")
        return fallback
    return normalize_model_decision(raw, prepared)


def apply_review_policy(decision: ReviewDecision, state: SupervisorState, prepared: PreparedReview,
                        service: ReviewService) -> ReviewDecision:
    if state.get("claimed_events"):
        return ReviewDecision("READ_INBOX", "claimed events must be incorporated before other work", "deterministic",
                              pending_intervention=state.get("pending_intervention"))
    if decision.action == "READ_INBOX":
        decision = ReviewDecision("DONE", "no claimed snapshot is available", decision.source)
    last_intervention = parse_timestamp(state.get("last_intervention_at"))
    recent = bool(last_intervention and (prepared.evaluation_timestamp - last_intervention).total_seconds()
                  < service.stagnation_threshold_seconds)
    if decision.action == "INTERVENE" and recent and not state.get("pending_intervention"):
        decision = ReviewDecision("DONE", "Stagnation intervention already sent during the current policy window", "deterministic")
    if service.stagnation_enabled and decision.action == "DONE" and not state.get("pending_intervention"):
        if recent:
            decision = ReviewDecision("DONE", "Stagnation intervention already sent during the current policy window", decision.source)
        else:
            decision = ReviewDecision("INTERVENE", f"No meaningful progress for {prepared.progress['stalled_for_ms'] / 1000:.0f}s", "deterministic",
                                      pending_intervention={"action": "message", "message": "No meaningful progress has been observed for the configured interval. Stop repeating the current approach, choose a different path, and report the next concrete result."})
    return decision if decision.action in VALID_ACTIONS else ReviewDecision("DONE", "invalid model action", decision.source)


def build_result(decision: ReviewDecision, state: SupervisorState, prepared: PreparedReview) -> dict[str, Any]:
    return {
        "assessment": {"action": decision.action, "reason": decision.reason, "pending_count": prepared.pending_count,
                       "decision_source": decision.source, "progress": prepared.progress},
        "next_action": decision.action, "session_status": "active" if decision.action != "DONE" else "idle",
        "_review_context": prepared.context, "opencode_context": prepared.opencode_context,
        "memory_manifest": prepared.memory.manifest, "session_memory_category_id": prepared.memory.session_category_id,
        "memory_operation": decision.memory_operation, "memory_link_request": decision.memory_link_request,
        "pending_intervention": decision.pending_intervention or state.get("pending_intervention"),
        "retrieved_memories": prepared.memory.retrieved_memories, "memory_read_status": prepared.memory.read_status,
        "memory_read_query": prepared.memory.read_query, "memory_read_error": prepared.memory.read_error,
        "evaluation_timestamp": prepared.evaluation_timestamp.isoformat(), **prepared.progress,
    }


def log_decision(decision: ReviewDecision, prepared: PreparedReview) -> None:
    emit(logger, logging.INFO, "review_decision", node="REVIEW", action=decision.action,
         pending_count=prepared.pending_count, reason=decision.reason, source=decision.source,
         decision_source=decision.source, supervisor_tick=prepared.tick,
         stalled_for_ms=prepared.progress["stalled_for_ms"])
