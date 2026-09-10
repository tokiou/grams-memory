"""Console logging and timing helpers for the Supervisor runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


_HANDLER_MARKER = "_grams_supervisor_handler"
_TRACE = ContextVar("grams_graph_trace", default=None)
_ANSI = "\x1b["
_COLORS = {
    logging.DEBUG: "90m",
    logging.INFO: "32m",
    logging.WARNING: "33m",
    logging.ERROR: "31m",
    logging.CRITICAL: "1;35m",
}
_SECRET_PATTERN = re.compile(
    r"(?i)([\"']?(?:authorization|api[_-]?key|token|password)[\"']?\s*[:=]\s*(?:[\"']?))"
    r"[^\s,;}\"']+"
)
_SENSITIVE_FIELDS = {
    "payload", "context", "opencode_context", "memory_manifest", "arguments",
    "messages", "headers", "response", "result", "api_key", "token",
}
_correlation: ContextVar[dict[str, Any]] = ContextVar("grams_log_correlation", default={})
_RESERVED_RECORD_FIELDS = set(logging.LogRecord(None, 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def monotonic_ns() -> int:
    """Return a monotonic timestamp without exposing clock values in logs."""
    import time

    return time.monotonic_ns()


def elapsed_ms(start_ns: int) -> float:
    return round((monotonic_ns() - start_ns) / 1_000_000, 1)


def sanitize(value: Any, *, limit: int = 240) -> str:
    text = str(value).replace("\n", "\\n").replace("\r", "\\r")
    text = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;}\"']+", r"\1[REDACTED]", text)
    text = _SECRET_PATTERN.sub(r"\1[REDACTED]", text)
    return text[:limit] + ("..." if len(text) > limit else "")


def parse_source_timestamp(payload: Any) -> datetime | None:
    """Read only the normalized OpenCode ``timestamp`` field."""
    if not isinstance(payload, dict) or not isinstance(payload.get("timestamp"), str):
        return None
    raw = payload["timestamp"]
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def source_lag_info(payload: Any, *, now: datetime | None = None) -> tuple[float | None, str]:
    if not isinstance(payload, dict) or "timestamp" not in payload:
        return None, "missing"
    raw = payload.get("timestamp")
    if not isinstance(raw, str):
        return None, "invalid"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid"
    if parsed.tzinfo is None:
        return None, "naive"
    current = now or datetime.now(timezone.utc)
    lag = (current - parsed.astimezone(timezone.utc)).total_seconds() * 1000
    if lag < 0:
        return None, "future_skew"
    return round(lag, 1), "valid"


def source_lag_ms(payload: Any, *, now: datetime | None = None) -> float | None:
    return source_lag_info(payload, now=now)[0]


def event_correlation(event: Any) -> dict[str, Any]:
    return {
        "event_id": getattr(event, "id", None),
        "session_id": getattr(event, "session_id", None),
        "root_session_id": getattr(event, "root_session_id", None),
        "event_type": getattr(event, "type", None),
        "source_event": getattr(event, "source_event", None),
        "source_run_id": getattr(event, "source_run_id", None) or getattr(event, "run_id", None),
        "ingress_id": getattr(event, "ingress_id", None),
        "retry_count": getattr(event, "retry_count", None),
    }


def begin_node(node: str) -> tuple[int, int | None, float | None]:
    trace = _TRACE.get()
    if trace is None:
        trace = {"step": 0, "last_finished_ns": None, "last_node": None, "nodes": {}}
        _TRACE.set(trace)
    now = monotonic_ns()
    previous_finished = trace["last_finished_ns"]
    gap_ms = round((now - previous_finished) / 1_000_000, 1) if previous_finished is not None else None
    trace["step"] += 1
    trace["nodes"][node] = trace["nodes"].get(node, 0) + 1
    return trace["step"], trace["last_node"], gap_ms


def finish_node(node: str, started_ns: int) -> float:
    trace = _TRACE.get()
    if trace is not None:
        trace["last_finished_ns"] = monotonic_ns()
        trace["last_node"] = node
    return elapsed_ms(started_ns)


def trace_summary() -> dict[str, Any]:
    trace = _TRACE.get()
    if trace is None:
        return {"steps": 0, "node_counts": {}}
    return {"steps": trace["step"], "node_counts": dict(trace["nodes"])}


@contextmanager
def bind_correlation(**fields: Any):
    token = _correlation.set({**_correlation.get(), **{key: value for key, value in fields.items() if value is not None}})
    try:
        yield
    finally:
        _correlation.reset(token)


@contextmanager
def graph_trace():
    token = _TRACE.set({"step": 0, "last_finished_ns": None, "last_node": None, "nodes": {}})
    try:
        yield
    finally:
        _TRACE.reset(token)


def safe_endpoint(value: str | None) -> str | None:
    if not value:
        return None
    from urllib.parse import urlsplit

    parsed = urlsplit(value)
    host = parsed.hostname or "unknown"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port:
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"


def emit(logger: logging.Logger, level: int, event_name: str, **fields: Any) -> None:
    """Emit a structured LogRecord while keeping values safe for console output."""
    merged = {**_correlation.get(), **fields}
    safe_fields = {}
    for key, value in merged.items():
        if value is None or key in _SENSITIVE_FIELDS or key in _RESERVED_RECORD_FIELDS or key == "event_name":
            continue
        safe_fields[key] = sanitize(value) if isinstance(value, str) else value
    logger.log(level, event_name, extra={"event_name": event_name, **safe_fields})


class ColoredFormatter(logging.Formatter):
    """Human-readable key/value logs with optional ANSI level colors."""

    def __init__(self, *, color: bool) -> None:
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds")
        level = record.levelname
        if self.color:
            level = f"{_ANSI}{_COLORS.get(record.levelno, '0m')}{level}{_ANSI}0m"
        event_name = getattr(record, "event_name", record.getMessage())
        fields = []
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_FIELDS or key.startswith("_") or key == "event_name" or key in _SENSITIVE_FIELDS:
                continue
            fields.append(f"{key}={sanitize(value)}")
        suffix = f" {' '.join(fields)}" if fields else ""
        return f"{timestamp} {level:<8} {event_name}{suffix}"


def configure_logging(level: str = "INFO", color: str = "auto", log_file: str | None = None) -> None:
    """Configure one idempotent console handler for the Supervisor."""
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)
            handler.close()
    use_color = color == "always" or (color == "auto" and bool(getattr(sys.stderr, "isatty", lambda: False)()))
    handler = logging.StreamHandler()
    setattr(handler, _HANDLER_MARKER, True)
    handler.setFormatter(ColoredFormatter(color=use_color))
    root.addHandler(handler)
    if log_file:
        from pathlib import Path

        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        setattr(file_handler, _HANDLER_MARKER, True)
        file_handler.setFormatter(ColoredFormatter(color=False))
        root.addHandler(file_handler)
