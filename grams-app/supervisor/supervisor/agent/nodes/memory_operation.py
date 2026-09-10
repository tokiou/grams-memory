"""Validated dispatcher for the real MCP memory tools."""

from typing import Any
import logging

from ...memory import MemoryClient
from ...memory.manifest import categories
from ..state import SupervisorState
from ...observability import emit

logger = logging.getLogger(__name__)

OPERATIONS = {
    "search": "search",
    "get": "get",
    "create": "create",
    "update": "update",
    "neighbors": "neighbors",
    "expand": "expand",
    "link": "link",
    "archive": "archive",
    "restore": "restore",
}


def _result_id(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    value = result.get("id") or result.get("ID")
    return str(value) if value else None


def _normalize_confidence(arguments: dict[str, Any]) -> None:
    value = arguments.get("confidence")
    if isinstance(value, str):
        labels = {"low": 0.3, "medium": 0.6, "high": 0.9}
        if value.lower() in labels:
            arguments["confidence"] = labels[value.lower()]
            return
        try:
            value = float(value)
        except ValueError:
            arguments.pop("confidence", None)
            return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        arguments["confidence"] = max(0.0, min(1.0, float(value)))


def _normalize_memory_enums(arguments: dict[str, Any], *, creating: bool) -> None:
    allowed = {
        "type": {
            "FACT", "OBSERVATION", "HYPOTHESIS", "CONSTRAINT", "ACTION",
            "ERROR", "DECISION", "STATE", "RESULT",
        },
        "status": {
            "ACTIVE", "TENTATIVE", "CONFIRMED", "REJECTED", "SUPERSEDED",
            "RESOLVED", "FAILED", "BLOCKED", "VALIDATED",
        },
        "graph_tier": {"ACTIVE", "COLD"},
    }
    defaults = {"type": "OBSERVATION", "status": "ACTIVE", "graph_tier": "ACTIVE"}
    for name, values in allowed.items():
        value = arguments.get(name)
        if isinstance(value, str):
            normalized = value.upper()
            if normalized in values:
                arguments[name] = normalized
            else:
                arguments.pop(name, None)
        if creating and name not in arguments:
            arguments[name] = defaults[name]


def _normalize_edge_enums(arguments: dict[str, Any]) -> None:
    allowed = {
        "relation": {
            "SUPPORTS", "CONTRADICTS", "TESTED_BY", "PRODUCED", "SUCCEEDED_WITH",
            "FAILED_BECAUSE", "BLOCKED_BY", "DEPENDS_ON", "SUPERSEDES", "VALIDATES",
        },
        "evidence_strength": {"WEAK", "MEDIUM", "STRONG"},
    }
    for name, values in allowed.items():
        value = arguments.get(name)
        if isinstance(value, str):
            normalized = value.upper()
            if normalized in values:
                arguments[name] = normalized
            else:
                arguments.pop(name, None)


async def _require_scoped_memories(memory: MemoryClient, memory_ids: list[str], manifest: dict[str, Any]) -> None:
    allowed_categories = {str(item.get("id")) for item in categories(manifest) if item.get("id")}
    if not allowed_categories:
        raise ValueError("memory operation has no verified session scope")
    for memory_id in memory_ids:
        item = await memory.get(str(memory_id))
        if not item or str(item.get("category_id")) not in allowed_categories:
            raise ValueError(f"memory_id is not present in the current memory scope: {memory_id}")


def make_memory_operation_node(memory: MemoryClient, category_id: str | None = None):
    async def memory_operation(state: SupervisorState) -> dict[str, Any]:
        request = state.get("memory_operation") or {}
        operation = str(request.get("operation", "")).lower()
        method_name = OPERATIONS.get(operation)
        if method_name is None:
            raise ValueError(f"unsupported memory operation: {operation}")

        arguments = dict(request.get("arguments") or {})
        scoped_category_id = state.get("session_memory_category_id") or category_id
        session_scoped = state.get("root_session_id") not in (None, "default")
        manifest = state.get("memory_manifest") or {"projects": []}
        if session_scoped and not scoped_category_id:
            raise ValueError("session memory hierarchy is unavailable")
        if operation in {"create", "update"}:
            _normalize_confidence(arguments)
            _normalize_memory_enums(arguments, creating=operation == "create")
            # The category ID already identifies its project/key in the MCP
            # schema; keep hierarchy IDs for review/search scope but do not
            # send unsupported fields to memory_create/memory_update.
            arguments.pop("project_id", None)
            arguments.pop("key_id", None)
        elif operation == "link":
            _normalize_confidence(arguments)
            _normalize_edge_enums(arguments)
        if operation == "search":
            arguments.setdefault("query", "")
            arguments.pop("root_session_id", None)
            if scoped_category_id and not arguments.get("category_id"):
                arguments["category_id"] = scoped_category_id
        elif operation == "create" and scoped_category_id and not arguments.get("category_id"):
            arguments["category_id"] = scoped_category_id
        elif operation in {"get", "neighbors", "expand", "archive", "restore"}:
            arguments.setdefault("memory_id", arguments.pop("id", None))
            if not arguments.get("memory_id"):
                raise ValueError(f"memory {operation} requires memory_id")
            if session_scoped:
                await _require_scoped_memories(memory, [str(arguments["memory_id"])], manifest)
        elif operation == "link":
            arguments.setdefault("source", arguments.pop("source_id", None))
            arguments.setdefault("target", arguments.pop("target_id", None))
            source_id = arguments.get("source")
            target_id = arguments.get("target")
            if not source_id or not target_id:
                raise ValueError("memory link requires source and target memory IDs")
            if session_scoped:
                await _require_scoped_memories(memory, [str(source_id), str(target_id)], manifest)
            elif hasattr(memory, "get"):
                if await memory.get(str(source_id)) is None or await memory.get(str(target_id)) is None:
                    raise ValueError("memory link references a memory that does not exist")

        if operation == "create":
            result = await memory.create(arguments)
        elif operation == "update":
            memory_id = arguments.pop("id", arguments.pop("memory_id", None))
            if not memory_id:
                raise ValueError("memory update requires id")
            if session_scoped:
                await _require_scoped_memories(memory, [str(memory_id)], manifest)
            result = await memory.update(str(memory_id), arguments)
        else:
            result = await getattr(memory, method_name)(**arguments)
        emit(logger, logging.INFO, "memory_operation_completed", node="MEMORY_OPERATION", operation=operation)
        update: dict[str, Any] = {
            "memory_operation_result": {"operation": operation, "result": result},
            "memory_operation": None,
            "next_action": "REVIEW",
        }
        link_request = state.get("memory_link_request")
        if operation == "create" and not link_request and state.get("retrieved_memories"):
            candidate = next((item for item in reversed(state["retrieved_memories"]) if _result_id(item)), None)
            if candidate:
                # A new observation must not become an orphan when search found
                # an existing candidate. The model may override this relation.
                link_request = {
                    "target_id": _result_id(candidate),
                    "relation": "SUPPORTS",
                    "confidence": 0.7,
                    "evidence_strength": "MEDIUM",
                    "direct": False,
                    "source": "supervisor_auto",
                }
        if operation == "create" and link_request:
            created_id = _result_id(result)
            if not created_id:
                raise RuntimeError("memory create returned no memory ID; refusing to continue without link tracking")
            link = dict(link_request)
            if created_id and link.get("target_id") and str(created_id) != str(link["target_id"]):
                update["memory_operation"] = {
                    "operation": "link",
                    "arguments": {
                        "source": str(created_id),
                        "target": str(link["target_id"]),
                        "relation": str(link.get("relation", "SUPPORTS")).upper(),
                        "confidence": link.get("confidence", 0.8),
                        "evidence_strength": str(link.get("evidence_strength", "MEDIUM")).upper(),
                        "direct": bool(link.get("direct", True)),
                    },
                }
                update["memory_link_request"] = None
                update["next_action"] = "REVIEW"
        if operation == "search":
            update["retrieved_memories"] = list(result or [])
        return update

    return memory_operation
