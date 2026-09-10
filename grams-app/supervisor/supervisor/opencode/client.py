"""Async HTTP client for the OpenCode server API."""

from typing import Any
from urllib.parse import quote
import logging

import httpx

from ..observability import elapsed_ms, emit, monotonic_ns

logger = logging.getLogger(__name__)


class OpenCodeClient:
    """Control an OpenCode session through its documented HTTP API."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout)

    def _session_path(self, session_id: str, suffix: str = "") -> str:
        return f"/session/{quote(session_id, safe='')}{suffix}"

    async def healthcheck(self) -> None:
        body = await self._request("GET", "/global/health")
        if not isinstance(body, dict) or body.get("healthy") is not True:
            raise RuntimeError("OpenCode health response was invalid")

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        started = monotonic_ns()
        operation = f"{method} {path}"
        emit(logger, logging.DEBUG, "external_call_started", service="opencode", operation=operation)
        try:
            response = await self._http.request(method, f"{self.base_url}{path}", **kwargs)
            response.raise_for_status()
            if not response.content:
                result = None
            else:
                result = response.json()
        except Exception as error:
            emit(logger, logging.ERROR, "external_call_failed", service="opencode", operation=operation,
                 outcome="error", error=type(error).__name__, duration_ms=elapsed_ms(started))
            raise
        emit(logger, logging.DEBUG, "external_call_completed", service="opencode", operation=operation,
             outcome="ok", status_code=response.status_code, duration_ms=elapsed_ms(started))
        return result

    async def get_context(self, session_id: str) -> dict[str, Any]:
        messages = await self._request("GET", self._session_path(session_id, "/message"), params={"limit": 20})
        if isinstance(messages, dict) and "data" in messages:
            messages = messages["data"]
        if not isinstance(messages, list):
            raise RuntimeError("OpenCode context response was invalid")
        return {"session_id": session_id, "messages": messages}

    async def inject_context(self, session_id: str, context: str) -> Any:
        return await self._request(
            "POST",
            self._session_path(session_id, "/prompt_async"),
            json={"parts": [{"type": "text", "text": context}]},
        )

    async def send_message(self, session_id: str, message: str) -> Any:
        return await self.inject_context(session_id, message)

    async def abort_session(self, session_id: str) -> Any:
        return await self._request("POST", self._session_path(session_id, "/abort"))

    async def task_control(self, session_id: str, action: str, **arguments: Any) -> Any:
        if action == "abort":
            await self.abort_session(session_id)
            return
        raise ValueError(f"OpenCode HTTP API does not expose task control action: {action}")

    async def aclose(self) -> None:
        await self._http.aclose()
