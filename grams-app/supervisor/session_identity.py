"""Validation helpers for session-scoped Supervisor operations."""


def validate_session_id(value: object, *, field_name: str = "session_id") -> str:
    """Return a real session identifier, rejecting empty and synthetic defaults."""
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value == "default"
    ):
        raise ValueError(f"{field_name} must be a non-empty, non-default session ID")
    return value
