"""Contracts shared by the Supervisor REVIEW components."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


VALID_ACTIONS = {"READ_INBOX", "MEMORY_OPERATION", "INTERVENE", "DONE"}


class ReviewMemory(Protocol):
    async def search(self, query: str = "", **filters: Any) -> list[dict[str, Any]]: ...
    async def get_manifest(self) -> dict[str, Any]: ...
    async def ensure_session_hierarchy(self, root_session_id: str) -> str: ...


class ReviewModel(Protocol):
    async def decide(self, context: dict[str, Any]) -> dict[str, Any]: ...


class ReviewDecisionError(RuntimeError):
    """The review model did not return a usable decision."""


@dataclass
class ReviewDecision:
    action: str
    reason: str
    source: str
    memory_operation: dict[str, Any] | None = None
    memory_link_request: dict[str, Any] | None = None
    pending_intervention: dict[str, Any] | None = None


@dataclass
class ReviewMemoryContext:
    manifest: dict[str, Any]
    session_category_id: str | None
    read_status: str
    read_query: str | None
    read_error: str | None
    retrieved_memories: list[dict[str, Any]]


@dataclass
class PreparedReview:
    evaluation_timestamp: datetime
    tick: bool
    pending_count: int
    context: dict[str, Any]
    progress: dict[str, Any]
    memory: ReviewMemoryContext
    opencode_context: dict[str, Any]
