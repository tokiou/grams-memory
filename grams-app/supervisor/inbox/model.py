"""Inbox event domain objects."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
import uuid

from supervisor.session_identity import validate_session_id


class EventStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SupervisorEventInput:
    payload: Any
    type: str
    source_event: str | None
    session_id: str
    root_session_id: str
    id: str | None = None
    run_id: str | None = None
    instance_id: str | None = None
    sequence: int | None = None
    received_at: datetime | None = None
    ingress_id: str = ""

    def __post_init__(self) -> None:
        validate_session_id(self.session_id)
        validate_session_id(self.root_session_id, field_name="root_session_id")

    @classmethod
    def from_payload(cls, payload: Any) -> "SupervisorEventInput":
        data = payload if isinstance(payload, dict) else {}
        event_type = data.get("type")
        session_id = validate_session_id(data.get("session_id"))
        root_session_value = data.get("root_session_id")
        root_session_id = (
            session_id
            if root_session_value is None
            else validate_session_id(root_session_value, field_name="root_session_id")
        )
        return cls(
            payload=payload,
            type=event_type if isinstance(event_type, str) and event_type else "UNKNOWN",
            source_event=data.get("source_event") if isinstance(data.get("source_event"), str) else None,
            session_id=session_id,
            root_session_id=root_session_id,
            id=data.get("id") if isinstance(data.get("id"), str) else None,
            run_id=data.get("run_id") if isinstance(data.get("run_id"), str) else None,
            instance_id=data.get("instance_id") if isinstance(data.get("instance_id"), str) else None,
            sequence=data.get("sequence") if isinstance(data.get("sequence"), int) else None,
            received_at=utcnow(),
            ingress_id=str(uuid.uuid4()),
        )

    def durable_id(self) -> str:
        return self.id or str(uuid.uuid4())


@dataclass(frozen=True)
class SupervisorEvent:
    id: str
    session_id: str | None
    root_session_id: str
    type: str
    source_event: str | None
    payload: Any
    status: EventStatus
    received_at: datetime
    processing_at: datetime | None = None
    processed_at: datetime | None = None
    run_id: str | None = None
    instance_id: str | None = None
    sequence: int | None = None
    retry_count: int = 0
    error: str | None = None
    lease_id: str | None = None
    lease_until: datetime | None = None
    source_run_id: str | None = None
    ingress_id: str | None = None
    cycle_id: str | None = None
