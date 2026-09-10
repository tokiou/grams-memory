"""Operational state checkpointed by LangGraph."""

from typing import Any, TypedDict


class SupervisorState(TypedDict, total=False):
    root_session_id: str
    supervisor_checkpoint_id: str
    run_id: str
    original_task: str | None
    current_activity: str | None
    current_tool: str | None
    current_task: str | None
    current_events: list[dict[str, Any]]
    claimed_events: list[dict[str, Any]]
    last_seen_event_id: str | None
    retrieved_memories: list[dict[str, Any]]
    memory_manifest: dict[str, Any]
    session_memory_category_id: str | None
    assessment: dict[str, Any]
    next_action: str
    pending_intervention: dict[str, Any] | None
    last_intervention: dict[str, Any] | None
    session_status: str
    processing_events: list[dict[str, Any]]
    memory_operation: dict[str, Any] | None
    memory_operation_result: dict[str, Any] | None
    memory_link_request: dict[str, Any] | None
    opencode_context: dict[str, Any] | None
    inbox_attention: bool
    _review_context: dict[str, Any]
    activity_started_at: str | None
    last_progress_at: str | None
    progress_count: int
    last_progress_kind: str | None
    evaluation_timestamp: str | None
    elapsed_ms: float
    stalled_for_ms: float
    supervisor_tick: bool
    memory_read_status: str
    memory_read_query: str | None
    memory_read_error: str | None
    activity_started_monotonic: float | None
    last_progress_monotonic: float | None
    last_intervention_at: str | None
