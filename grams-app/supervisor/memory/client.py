"""Async Streamable HTTP adapter for the Go Memory MCP server."""

from typing import Any, Protocol
import asyncio
import json
import logging
from contextlib import suppress

import httpx

from ..observability import elapsed_ms, emit, monotonic_ns

logger = logging.getLogger(__name__)


def _field(value: dict[str, Any], name: str) -> Any:
    if name in value:
        return value[name]
    normalized = name.lower().replace("_", "")
    return next((item for key, item in value.items() if str(key).lower().replace("_", "") == normalized), None)


class MemoryClient(Protocol):
    async def search(self, query: str = "", **filters: Any) -> list[dict[str, Any]]: ...
    async def get(self, memory_id: str) -> dict[str, Any] | None: ...
    async def neighbors(self, memory_id: str, **filters: Any) -> dict[str, Any]: ...
    async def expand(self, memory_id: str, **filters: Any) -> dict[str, Any]: ...
    async def create(self, memory: dict[str, Any]) -> dict[str, Any]: ...
    async def update(self, memory_id: str, memory: dict[str, Any]) -> dict[str, Any]: ...
    async def link(self, source: str, target: str, relation: str, **metadata: Any) -> dict[str, Any]: ...
    async def archive(self, memory_id: str) -> dict[str, Any]: ...
    async def restore(self, memory_id: str) -> dict[str, Any]: ...

    async def ensure_session_hierarchy(self, root_session_id: str) -> str: ...

    async def get_manifest(self) -> dict[str, Any]: ...


class MCPMemoryClient:
    """MCP JSON-RPC client for a Streamable HTTP endpoint.

    The Go server is stateless, but the client still performs the standard MCP
    initialize handshake and keeps a session header if the server returns one.
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout)
        self._request_id = 0
        self._session_id: str | None = None
        self._initialized = False
        self._lock = asyncio.Lock()

    async def _request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        started = monotonic_ns()
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method}
        if params is not None:
            payload["params"] = params
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        operation = method
        if method == "tools/call" and isinstance(params, dict):
            operation = f"{method}:{params.get('name', 'unknown')}"
        emit(logger, logging.DEBUG, "external_call_started", service="mcp", operation=operation)
        try:
            response = await self._http.post(self.base_url, json=payload, headers=headers)
            response.raise_for_status()
            session_id = response.headers.get("Mcp-Session-Id")
            if session_id:
                self._session_id = session_id
            body = response.json()
            if not isinstance(body, dict) or ("result" not in body and "error" not in body):
                raise RuntimeError(f"Memory MCP {method} returned an invalid JSON-RPC response")
            if "error" in body:
                error = body["error"]
                if isinstance(error, dict):
                    code = str(error.get("code", "unknown"))[:40]
                    message = str(error.get("message", "request failed"))[:500]
                else:
                    code, message = "unknown", "request failed"
                raise RuntimeError(f"Memory MCP {method} failed ({code}): {message}")
            result = body.get("result")
        except Exception as error:
            emit(logger, logging.ERROR, "external_call_failed", service="mcp", operation=operation,
                 outcome="error", error=type(error).__name__, duration_ms=elapsed_ms(started))
            raise
        emit(logger, logging.DEBUG, "external_call_completed", service="mcp", operation=operation,
             outcome="ok", status_code=response.status_code, duration_ms=elapsed_ms(started))
        return result

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        started = monotonic_ns()
        emit(logger, logging.DEBUG, "external_call_started", service="mcp", operation=method)
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        try:
            response = await self._http.post(self.base_url, json=payload, headers=headers)
            response.raise_for_status()
        except Exception as error:
            emit(logger, logging.ERROR, "external_call_failed", service="mcp", operation=method,
                 outcome="error", error=type(error).__name__, duration_ms=elapsed_ms(started))
            raise
        emit(logger, logging.DEBUG, "external_call_completed", service="mcp", operation=method,
             outcome="ok", status_code=response.status_code, duration_ms=elapsed_ms(started))

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            await self._request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "grams-supervisor", "version": "0.1.0"},
                },
            )
            await self._notify("notifications/initialized")
            self._initialized = True

    async def list_tools(self) -> list[dict[str, Any]]:
        await self._ensure_initialized()
        result = await self._request("tools/list")
        return list(result.get("tools", [])) if isinstance(result, dict) else list(result or [])

    async def ensure_session_hierarchy(self, root_session_id: str) -> str:
        """Create the session project and its small, reusable memory taxonomy."""
        projects = list(await self._call_tool("project_list", {}) or [])
        project = next((item for item in projects if _field(item, "name") == root_session_id), None)
        if project is None:
            project = await self._call_tool("project_create", {
                "name": root_session_id,
                "description": f"OpenCode session memory for {root_session_id}",
            })
        project_id = _field(project, "id")
        keys = list(await self._call_tool("key_list", {"id": project_id}) or [])
        taxonomy = {
            "objective": ("requirements", "constraints"),
            "execution": ("discoveries", "attempts", "errors", "decisions", "progress"),
            "results": ("validations", "outcomes"),
        }
        default_category_id: str | None = None
        for key_name, category_names in taxonomy.items():
            key = next((item for item in keys if _field(item, "name") == key_name), None)
            if key is None:
                key = await self._call_tool("key_create", {
                    "project_id": project_id,
                    "name": key_name,
                    "description": f"{key_name.title()} knowledge for {root_session_id}",
                })
            key_id = _field(key, "id")
            categories = list(await self._call_tool("category_list", {"id": key_id}) or [])
            for category_name in category_names:
                category = next((item for item in categories if _field(item, "name") == category_name), None)
                if category is None:
                    category = await self._call_tool("category_create", {
                        "key_id": key_id,
                        "name": category_name,
                        "description": f"{category_name.title()} for {root_session_id}",
                    })
                if key_name == "execution" and category_name == "progress":
                    default_category_id = str(_field(category, "id"))
        if default_category_id is None:
            raise RuntimeError("session memory hierarchy did not create an execution/progress category")
        return default_category_id

    async def get_manifest(self) -> dict[str, Any]:
        manifest: dict[str, Any] = {"projects": []}
        for project in list(await self._call_tool("project_list", {}) or []):
            project_id = _field(project, "id")
            project_entry = {
                "id": project_id,
                "name": _field(project, "name"),
                "description": _field(project, "description") or "",
                "keys": [],
            }
            for key in list(await self._call_tool("key_list", {"id": project_id}) or []):
                key_id = _field(key, "id")
                key_entry = {
                    "id": key_id,
                    "name": _field(key, "name"),
                    "description": _field(key, "description") or "",
                    "categories": [],
                }
                for category in list(await self._call_tool("category_list", {"id": key_id}) or []):
                    key_entry["categories"].append({
                        "id": _field(category, "id"),
                        "name": _field(category, "name"),
                        "description": _field(category, "description") or "",
                    })
                project_entry["keys"].append(key_entry)
            manifest["projects"].append(project_entry)
        return manifest

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        await self._ensure_initialized()
        result = await self._request("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            return result
        if result.get("isError") is True:
            content = result.get("content") or []
            message = next((item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"), "tool call failed")
            raise RuntimeError(f"Memory MCP {name} failed: {str(message)[:500]}")
        if result.get("structuredContent") is not None:
            structured = result["structuredContent"]
            if isinstance(structured, dict) and "result" in structured:
                return structured["result"]
            return structured
        content = result.get("content", [])
        if not isinstance(content, list) or not content:
            raise RuntimeError(f"Memory MCP {name} returned an empty tool result")
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                with suppress(json.JSONDecodeError):
                    return json.loads(item["text"])
                return item["text"]
        return content

    async def search(self, query: str = "", **filters: Any) -> list[dict[str, Any]]:
        supported = {key: value for key, value in filters.items() if key in {
            "project_id", "key_id", "category_id", "types", "statuses", "graph_tiers", "avoid", "min_confidence", "limit", "offset", "query"
        }}
        if query and "query" not in supported:
            supported["query"] = query
        result = await self._call_tool("memory_search", supported)
        if result is None:
            return []
        if not isinstance(result, list):
            raise RuntimeError("Memory MCP memory_search returned an invalid list")
        return result

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        result = await self._call_tool("memory_get", {"id": memory_id})
        if result is not None and not isinstance(result, dict):
            raise RuntimeError("Memory MCP memory_get returned an invalid memory")
        return result

    async def neighbors(self, memory_id: str, **filters: Any) -> dict[str, Any]:
        result = await self._call_tool("memory_neighbors", {"id": memory_id, **filters})
        if not isinstance(result, dict):
            raise RuntimeError("Memory MCP memory_neighbors returned an invalid subgraph")
        return result

    async def expand(self, memory_id: str, **filters: Any) -> dict[str, Any]:
        result = await self._call_tool("memory_expand", {"id": memory_id, **filters})
        if not isinstance(result, dict):
            raise RuntimeError("Memory MCP memory_expand returned an invalid subgraph")
        return result

    async def create(self, memory: dict[str, Any]) -> dict[str, Any]:
        result = await self._call_tool("memory_create", memory)
        if not isinstance(result, dict):
            raise RuntimeError("Memory MCP memory_create returned an invalid memory")
        return result

    async def update(self, memory_id: str, memory: dict[str, Any]) -> dict[str, Any]:
        existing = await self.get(memory_id)
        if not isinstance(existing, dict):
            raise RuntimeError(f"Memory MCP memory_update could not load {memory_id}")
        payload = {"id": memory_id, **memory}
        payload.setdefault("category_id", _field(existing, "category_id"))
        payload.setdefault("content", _field(existing, "content"))
        result = await self._call_tool("memory_update", payload)
        if not isinstance(result, dict):
            raise RuntimeError("Memory MCP memory_update returned an invalid memory")
        return result

    async def link(self, source: str, target: str, relation: str, **metadata: Any) -> dict[str, Any]:
        result = await self._call_tool("memory_link", {"source_id": source, "target_id": target, "relation": relation, **metadata})
        if not isinstance(result, dict):
            raise RuntimeError("Memory MCP memory_link returned an invalid edge")
        return result

    async def archive(self, memory_id: str) -> dict[str, Any]:
        result = await self._call_tool("memory_archive", {"id": memory_id})
        if not isinstance(result, dict):
            raise RuntimeError("Memory MCP memory_archive returned an invalid memory")
        return result

    async def restore(self, memory_id: str) -> dict[str, Any]:
        result = await self._call_tool("memory_restore", {"id": memory_id})
        if not isinstance(result, dict):
            raise RuntimeError("Memory MCP memory_restore returned an invalid memory")
        return result

    async def aclose(self) -> None:
        if self._session_id:
            with suppress(Exception):
                response = await self._http.delete(
                    self.base_url,
                    headers={"Mcp-Session-Id": self._session_id},
                )
                response.raise_for_status()
        await self._http.aclose()
