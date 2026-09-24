"""Request schemas for the Supervisor API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from supervisor.session_identity import validate_session_id


class EventRequest(BaseModel):
    """Version 1 event envelope emitted by the GRAMS OpenCode plugin."""

    model_config = ConfigDict(extra="allow", strict=True)

    schema_version: Literal[1]
    type: str = Field(min_length=1)
    source_event: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    payload: Any
    id: str | None = None
    root_session_id: str | None = None
    run_id: str | None = None
    instance_id: str | None = None
    sequence: int | None = None

    @field_validator("session_id")
    @classmethod
    def validate_session(cls, value: str) -> str:
        return validate_session_id(value)

    @field_validator("root_session_id")
    @classmethod
    def validate_root_session(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_session_id(value, field_name="root_session_id")

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("timestamp must be an ISO-8601 datetime") from error
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value
