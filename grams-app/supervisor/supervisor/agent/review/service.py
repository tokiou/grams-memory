"""Memory access and progress tracking for REVIEW."""

from datetime import datetime, timezone
from typing import Any
import logging
import time

from ...observability import emit
from ..state import SupervisorState
from .schema import ReviewMemory

logger = logging.getLogger(__name__)


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def progress_metrics(state: SupervisorState, now: datetime, *, tick: bool, threshold_seconds: float) -> dict[str, Any]:
    current = state.get("current_events", [])
    started = parse_timestamp(state.get("activity_started_at"))
    if started is None:
        timestamps = [parse_timestamp(event.get("received_at")) for event in current]
        timestamps = [value for value in timestamps if value is not None]
        started = min(timestamps) if timestamps else None
    last_progress = parse_timestamp(state.get("last_progress_at"))
    anchor = last_progress or started
    current_monotonic = time.monotonic()
    started_monotonic = state.get("activity_started_monotonic")
    progress_monotonic = state.get("last_progress_monotonic")
    elapsed_ms = ((current_monotonic - started_monotonic) * 1000
                  if isinstance(started_monotonic, (int, float)) and current_monotonic >= started_monotonic
                  else ((now - started).total_seconds() * 1000 if started else 0.0))
    stalled_for_ms = ((current_monotonic - progress_monotonic) * 1000
                      if isinstance(progress_monotonic, (int, float)) and current_monotonic >= progress_monotonic
                      else ((now - anchor).total_seconds() * 1000 if anchor else 0.0))
    return {
        "activity_started_at": started.isoformat() if started else None,
        "last_progress_at": last_progress.isoformat() if last_progress else None,
        "progress_count": int(state.get("progress_count", 0)),
        "last_progress_kind": state.get("last_progress_kind"),
        "elapsed_ms": round(elapsed_ms, 1),
        "stalled_for_ms": round(stalled_for_ms, 1),
        "stalled": bool(anchor and stalled_for_ms >= threshold_seconds * 1000),
        "supervisor_tick": tick,
        "activity_started_monotonic": started_monotonic,
        "last_progress_monotonic": progress_monotonic,
    }


def session_project(manifest: dict[str, Any], root_session_id: str) -> dict[str, Any] | None:
    return next((project for project in manifest.get("projects", [])
                 if project.get("name") == root_session_id), None)


class ReviewService:
    """Prepares review context and keeps policy decisions testable."""

    def __init__(self, memory: ReviewMemory | None, *, stagnation_enabled: bool = True,
                 stagnation_threshold_seconds: float = 1800.0) -> None:
        self.memory = memory
        self.stagnation_enabled = stagnation_enabled
        self.stagnation_threshold_seconds = stagnation_threshold_seconds

    def progress(self, state: SupervisorState, now: datetime, *, tick: bool) -> dict[str, Any]:
        return progress_metrics(state, now, tick=tick, threshold_seconds=self.stagnation_threshold_seconds)

    async def read_session_memories(
        self,
        state: SupervisorState,
        manifest: dict[str, Any],
        root_session_id: str,
        *,
        tick: bool,
    ) -> tuple[str, str | None, str | None, list[dict[str, Any]]]:
        status = state.get("memory_read_status", "NOT_STARTED")
        query = state.get("memory_read_query")
        error_text = state.get("memory_read_error")
        memories = list(state.get("retrieved_memories", []))
        if status == "COMPLETED" and not tick:
            return status, query, error_text, memories
        if self.memory is None:
            return status, query, error_text, memories
        project = session_project(manifest, root_session_id)
        project_id = project.get("id") if project else None
        if not project_id:
            raise RuntimeError(f"memory project is missing for session {root_session_id}")
        query = state.get("original_task") or state.get("current_task") or state.get("current_activity") or "supervisor operational context"
        emit(logger, logging.INFO, "memory_read_started", node="REVIEW",
             root_session_id=root_session_id, query=query, tick=tick)
        try:
            memories = await self.memory.search(query, project_id=project_id, limit=20)
        except Exception as error:
            error_text = f"{type(error).__name__}: {error}"
            emit(logger, logging.ERROR, "memory_read_failed", node="REVIEW",
                 root_session_id=root_session_id, error=type(error).__name__, detail=str(error), tick=tick)
            return "FAILED", query, error_text, []
        emit(logger, logging.INFO, "memory_read_completed", node="REVIEW",
             root_session_id=root_session_id, memory_count=len(memories), tick=tick)
        return "COMPLETED", query, None, memories
