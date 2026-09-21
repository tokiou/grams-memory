"""Deterministic construction of the only state sent to Jev."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import copy
import json

from supervisor.memory.client import _field
from supervisor.observability import sanitize


FINAL_EVENTS = {
    "REASONING_FINAL",
    "TEXT_FINAL",
    "TOOL_CALL_FINAL",
    "TOOL_RESULT_FINAL",
    "FILE_CHANGE_FINAL",
    "MESSAGE_COMPLETED",
    "MESSAGE_ERROR",
}


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bounded_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return sanitize(value.strip(), limit=limit)


def _canonical(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        field: field_value
        for field in fields
        if (field_value := _field(value, field)) is not None
    }


def _memory(value: Any) -> dict[str, Any]:
    return _canonical(value, (
        "id", "category_id", "title", "content", "description", "type", "status", "graph_tier",
        "confidence", "source", "created_at", "updated_at",
    ))


def _relation(value: Any) -> dict[str, Any]:
    return _canonical(value, (
        "id", "source_id", "target_id", "relation", "confidence", "evidence_strength", "direct",
    ))


def _expanded_context(value: Any) -> dict[str, Any] | None:
    expanded = _record(value)
    if not expanded:
        return None
    return {
        "memories": {
            str(memory_id): _memory(memory)
            for memory_id, memory in _record(expanded.get("memories")).items()
        },
        "subgraphs": {
            str(memory_id): {
                "nodes": [
                    _memory(item)
                    for item in (_field(_record(graph), "nodes") or [])
                    if isinstance(item, dict)
                ],
                "edges": [
                    _relation(item)
                    for item in (_field(_record(graph), "edges") or [])
                    if isinstance(item, dict)
                ],
            }
            for memory_id, graph in _record(expanded.get("subgraphs")).items()
        },
        "related_processes": {
            str(process_id): _canonical(process, (
                "id", "project_id", "key_id", "name", "status", "predecessor_id", "started_at", "closed_at",
            ))
            for process_id, process in _record(expanded.get("related_processes")).items()
        },
        "summaries": {
            str(process_id): _memory(summary)
            for process_id, summary in _record(expanded.get("summaries")).items()
        },
        "category_pages": {
            str(category): [_memory(item) for item in items if isinstance(item, dict)]
            for category, items in _record(expanded.get("category_pages")).items()
            if isinstance(items, list)
        },
    }


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Extract a compact execution event from the persisted plugin payload."""
    envelope_payload = _record(event.get("payload"))
    event_type = envelope_payload.get("type") or event.get("type")
    raw = _record(envelope_payload.get("payload", envelope_payload))
    properties = _record(raw.get("properties") or raw.get("data"))
    part = _record(properties.get("part") or raw.get("part"))
    hook_input = _record(raw.get("input"))
    hook_output = _record(raw.get("output"))

    normalized: dict[str, Any] = {
        "id": event.get("id"),
        "type": event_type,
        "timestamp": envelope_payload.get("timestamp") or event.get("received_at"),
    }
    text = part.get("text")
    if not isinstance(text, str):
        text = properties.get("text") or raw.get("text")
    if isinstance(text, str) and text.strip():
        normalized["text"] = text.strip()[:2000]

    tool_name = hook_input.get("tool") or hook_input.get("name") or properties.get("tool")
    if isinstance(tool_name, str) and tool_name:
        normalized["tool_name"] = tool_name[:100]
    arguments = (
        hook_input.get("args")
        or hook_input.get("arguments")
        or hook_input.get("input")
        or hook_output.get("args")
        or hook_output.get("arguments")
    )
    if isinstance(arguments, dict):
        argument_summary = {
            key: bounded
            for key in ("command", "description", "filePath", "path", "pattern", "query")
            if (bounded := _bounded_text(arguments.get(key), 1000)) is not None
        }
        if argument_summary:
            normalized["arguments"] = argument_summary
    elif (argument_text := _bounded_text(arguments, 1000)) is not None:
        normalized["arguments"] = argument_text
    result_text = hook_output.get("output") or hook_output.get("text") or hook_output.get("result")
    if (bounded_result := _bounded_text(result_text, 2000)) is not None:
        normalized["result"] = bounded_result
    metadata = _record(hook_output.get("metadata"))
    exit_code = hook_output.get("exit_code")
    if not isinstance(exit_code, int):
        exit_code = metadata.get("exit") if isinstance(metadata.get("exit"), int) else metadata.get("exit_code")
    if isinstance(exit_code, int):
        normalized["exit_code"] = exit_code
    success = hook_output.get("success")
    if not isinstance(success, bool):
        success = properties.get("success")
    if not isinstance(success, bool) and isinstance(exit_code, int):
        success = exit_code == 0
    if isinstance(success, bool):
        normalized["success"] = success

    path = properties.get("path") or raw.get("path")
    if isinstance(path, str) and path:
        normalized["artifacts_changed"] = [path]
    error = properties.get("error") or raw.get("error") or hook_output.get("error")
    if error is not None:
        normalized["error"] = str(error)[:500]
    return {key: value for key, value in normalized.items() if value is not None}


def _seconds_since(value: Any, now: datetime) -> int | None:
    parsed = _timestamp(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds()))


def _memory_time(memory: dict[str, Any]) -> datetime | None:
    return _timestamp(_field(memory, "updated_at") or _field(memory, "created_at"))


def build_jev_process_state(state: dict[str, Any]) -> dict[str, Any]:
    context = _record(state.get("process_context"))
    process = _record(context.get("process"))
    categories = _record(context.get("categories"))
    events = [
        normalize_event(event)
        for event in state.get("claimed_events", [])
        if isinstance(event, dict)
        and (event.get("type") in FINAL_EVENTS or _record(event.get("payload")).get("type") in FINAL_EVENTS)
    ]
    now = datetime.now(timezone.utc)
    strategy = [_memory(item) for item in categories.get("STRATEGY") or []]
    evidence = [_memory(item) for item in categories.get("EVIDENCE") or []]
    strategy_times = [value for item in strategy if (value := _memory_time(item)) is not None]
    evidence_times = [value for item in evidence if (value := _memory_time(item)) is not None]
    latest_strategy = max(strategy_times, default=None)
    latest_evidence = max(evidence_times, default=None)

    event_times = [_timestamp(event.get("timestamp")) for event in events]
    events_since_evidence = sum(
        timestamp is not None and (latest_evidence is None or timestamp > latest_evidence)
        for timestamp in event_times
    )
    validation_count = sum(
        event.get("type") == "TOOL_RESULT_FINAL"
        and event.get("success") is not False
        and any(
            token in str({
                "tool": event.get("tool_name"),
                "arguments": event.get("arguments"),
                "result": event.get("result"),
            }).lower()
            for token in ("test", "pytest", "verify", "check")
        )
        for event in events
    )
    process_age = process.get("age_seconds")
    if not isinstance(process_age, (int, float)):
        process_age = _seconds_since(_field(process, "started_at"), now)
    metrics = {
        "process_age_seconds": process_age or 0,
        "seconds_since_last_strategy_change": (
            max(0, int((now - latest_strategy).total_seconds())) if latest_strategy else process_age or 0
        ),
        "seconds_since_last_evidence": (
            max(0, int((now - latest_evidence).total_seconds())) if latest_evidence else process_age or 0
        ),
        "events_since_last_evidence": events_since_evidence,
        "recent_event_count": len(events),
        "recent_tool_call_count": sum(event.get("type") == "TOOL_CALL_FINAL" for event in events),
        "recent_file_change_count": sum(event.get("type") == "FILE_CHANGE_FINAL" for event in events),
        "recent_validation_count": validation_count,
        "recent_error_count": sum(
            event.get("type") == "MESSAGE_ERROR"
            or (event.get("type") == "TOOL_RESULT_FINAL" and event.get("success") is False)
            for event in events
        ),
        "context_truncated": any(
            bool(page.get("truncated"))
            for page in _record(context.get("category_pagination")).values()
            if isinstance(page, dict)
        ),
        "truncated_categories": [
            name
            for name, page in _record(context.get("category_pagination")).items()
            if isinstance(page, dict) and page.get("truncated")
        ],
    }
    current_process = {
        key: _field(process, key)
        for key in ("id", "name", "status", "started_at")
        if _field(process, key) is not None
    }
    current_process["age_seconds"] = metrics["process_age_seconds"]
    return {
        "task": {
            "objective": state.get("original_task", ""),
            "global_constraints": list(state.get("task_constraints") or []),
        },
        "current_process": current_process,
        "strategy": strategy,
        "evidence": evidence,
        "summary": _memory(context.get("summary")) if context.get("summary") else None,
        "relations": [_relation(item) for item in context.get("relations") or []],
        "recent_execution": events,
        "operational_metrics": metrics,
        "expanded_memory": _expanded_context(state.get("expanded_memory_context")),
    }


def estimate_json_tokens(value: Any) -> int:
    """Conservatively estimate tokens using UTF-8 byte-level tokenization."""
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return len(serialized.encode("utf-8"))


def compact_jev_state(state: dict[str, Any], *, max_tokens: int) -> dict[str, Any]:
    """Keep the highest-value portions of a Jev state within a token budget."""
    compacted = copy.deepcopy(state)
    omitted: dict[str, int] = {}

    def size() -> int:
        candidate = compacted
        if omitted:
            candidate = {
                **compacted,
                "context_compaction": {
                    "truncated": True,
                    "omitted_items": omitted,
                    "budget_tokens": max_tokens,
                },
            }
        return estimate_json_tokens(candidate)

    def drop_from(path: tuple[str, ...], key: str, *, from_end: bool = False) -> bool:
        target: Any = compacted
        for part in path:
            if not isinstance(target, dict):
                return False
            target = target.get(part)
        if isinstance(target, dict) and target:
            target.pop(sorted(target)[0])
        elif isinstance(target, list) and target:
            target.pop(-1 if from_end else 0)
        else:
            return False
        omitted[key] = omitted.get(key, 0) + 1
        return True

    # Expanded graph data and older execution events are optional first losses.
    drop_paths = [
        (("expanded_memory", "subgraphs"), "expanded_subgraphs", False),
        (("expanded_memory", "memories"), "expanded_memories", False),
        (("expanded_memory", "category_pages"), "expanded_category_pages", False),
        (("expanded_memory", "related_processes"), "expanded_related_processes", False),
        (("expanded_memory", "summaries"), "expanded_summaries", False),
        (("recent_execution",), "recent_execution", False),
        (("relations",), "relations", False),
        # Memory search orders these lists newest-first; preserve recent items.
        (("evidence",), "evidence", True),
        (("strategy",), "strategy", True),
    ]
    while size() > max_tokens:
        changed = False
        for path, key, from_end in drop_paths:
            if drop_from(path, key, from_end=from_end):
                changed = True
                break
        if not changed:
            break

    if omitted:
        compacted["context_compaction"] = {
            "truncated": True,
            "omitted_items": omitted,
            "budget_tokens": max_tokens,
        }
    return compacted
