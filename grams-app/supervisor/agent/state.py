"""Shared LangGraph state for the GRAMS Supervisor."""

from __future__ import annotations

from typing import Any, TypedDict

from supervisor.agent.schemas import (
    ClaimedInboxEvent,
    MemoryUpdateProposal,
    ProcessContinuityDecision,
    ProgressStall,
    ReviewDecision,
)


class SupervisorState(TypedDict, total=False):
    """Small coordination state; durable semantic memory remains in MCP."""

    # Identity and scope.
    root_session_id: str
    project_id: str
    original_task: str

    # Current Inbox cycle.
    claimed_events: list[ClaimedInboxEvent]

    # Current process snapshot.
    active_process_id: str
    process_context: dict[str, Any]

    # Process transition.
    process_continuity: ProcessContinuityDecision
    pending_process_transition: dict[str, Any]

    # Memory update proposal and application result.
    proposed_memory_update: MemoryUpdateProposal
    memory_update_result: dict[str, Any]

    # Operational signal.
    progress_stall: ProgressStall

    # Progressive retrieval.
    expanded_memory_context: dict[str, Any]
    memory_expansion_depth: int

    # Main decision.
    review_decision: ReviewDecision

    # Process closing.
    pending_process_summary: str

    # Intervention delivery.
    intervention_result: dict[str, Any]

    # Non-semantic operational diagnostics.
    cycle_errors: list[dict[str, Any]]
