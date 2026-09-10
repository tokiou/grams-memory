"""Main Supervisor REVIEW node orchestration."""

from typing import Any

from ...inbox import EventInbox
from ...opencode import OpenCodeClient
from ..state import SupervisorState
from ..review import (OpenAIReviewModel, PreparedReview, ReviewDecision, ReviewDecisionError,
                      ReviewMemory, ReviewMemoryContext, ReviewModel, ReviewService, VALID_ACTIONS,
                      apply_review_policy, build_result, deterministic_decision, fallback_action,
                      log_decision, normalize_model_decision, prepare_memory_context, prepare_review,
                      resolve_decision)


def make_review_node(
    inbox: EventInbox,
    opencode: OpenCodeClient,
    review_service: ReviewService,
    model: ReviewModel | None = None,
    *,
    memory_manifest: dict[str, Any] | None = None,
    memory_category_id: str | None = None,
):
    async def review(state: SupervisorState) -> dict[str, Any]:
        prepared = await prepare_review(
            inbox, opencode, review_service, state,
            memory_manifest=memory_manifest,
            memory_category_id=memory_category_id,
        )
        decision = deterministic_decision(state)
        if decision is None:
            decision = await resolve_decision(state, prepared, model)
        decision = apply_review_policy(decision, state, prepared, review_service)
        log_decision(decision, prepared)
        return build_result(decision, state, prepared)

    return review


__all__ = [
    "OpenAIReviewModel", "PreparedReview", "ReviewDecision", "ReviewDecisionError",
    "ReviewMemory", "ReviewMemoryContext", "ReviewModel", "ReviewService", "VALID_ACTIONS",
    "apply_review_policy", "build_result", "build_review_result", "deterministic_decision", "fallback_action",
    "make_review_node", "normalize_model_decision", "prepare_memory_context", "prepare_review",
    "resolve_decision",
]
