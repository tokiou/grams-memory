"""Shared LangGraph state for the GRAMS Supervisor."""

from __future__ import annotations

from typing import Any, TypedDict

from supervisor.agent.schemas import (
    ClaimedInboxEvent,
    FactualMemoryCandidate,
    MemoryCurationProposal,
    MemoryMaterialization,
    MemoryUpdateProposal,
    ProcessContinuityDecision,
    SupervisionDecision,
    ProcessSummary,
    SupervisionDiagnostics,
)
from supervisor.agent.state_builder import build_jev_process_state

__all__ = ["SupervisorState", "build_jev_process_state"]


class SupervisorState(TypedDict, total=False):
    """Small coordination state; durable semantic memory remains in MCP."""

    # Identity and scope.
    root_session_id: str
    project_id: str
    original_task: str
    task_constraints: list[str]

    # Current Inbox cycle.
    claimed_events: list[ClaimedInboxEvent]

    # Current process snapshot.
    active_process_id: str
    cycle_already_completed: bool
    process_context: dict[str, Any]
    context_reload_reason: str
    context_route: str

    # Process transition.
    process_continuity: ProcessContinuityDecision
    pending_process_transition: dict[str, Any]

    # Memory update proposal and application result.
    memory_candidates: list[FactualMemoryCandidate]
    memory_curation: MemoryCurationProposal
    memory_materializations: list[MemoryMaterialization]
    proposed_memory_update: MemoryUpdateProposal
    memory_update_result: dict[str, Any]

    # Jev diagnostics retained for the action call and observability.
    supervision_diagnostics: SupervisionDiagnostics

    # Progressive retrieval.
    expanded_memory_context: dict[str, Any]
    memory_expansion_depth: int
    memory_expansion_exhausted: bool

    # Main decision.
    supervision_decision: SupervisionDecision

    # Process closing.
    pending_process_summary: ProcessSummary
    closed_process: dict[str, Any]

    # Intervention delivery.
    selected_intervention_memories: list[dict[str, Any]]
    intervention_result: dict[str, Any]
    intervention_message: str
    acknowledged_event_ids: list[str]
    final_status: str
    health_route: str

    # Non-semantic operational diagnostics.
    cycle_errors: list[dict[str, Any]]
