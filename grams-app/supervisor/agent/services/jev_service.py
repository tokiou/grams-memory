"""TypeSafe Jev service for structured Supervisor decisions."""

from __future__ import annotations

import json
from typing import Any
import logging
import os

import httpx

from supervisor.observability import elapsed_ms, emit, monotonic_ns

logger = logging.getLogger(__name__)

class JevClient:
    """Async client for TypeSafe System One evaluations."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY", "")
        self.base_url = (base_url or os.getenv("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1")).rstrip("/")
        self.model = model or os.getenv("TYPESAFE_MODEL", "jev-latest")
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None

    async def system_one(
        self,
        *,
        state: Any,
        questions: dict[str, dict[str, Any]],
        model: str | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("TYPESAFE_API_KEY is required")
        selected_model = model or self.model
        started = monotonic_ns()
        request_body = {"model": selected_model, "state": state, "questions": questions}
        emit(logger, logging.INFO, "model_call_started", service="jev", model=selected_model,
             prompt_version="v2", questions=sorted(questions), question_count=len(questions))
        try:
            response = await self._http.post(
                f"{self.base_url}/systemone",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=request_body,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as error:
            error_fields = {
                "error": type(error).__name__,
                "duration_ms": elapsed_ms(started),
            }
            if isinstance(error, httpx.HTTPStatusError):
                response = error.response
                error_fields.update({
                    "status_code": response.status_code,
                    "request_id": response.headers.get("x-typesafe-request-id"),
                    "response_body": response.text[:4000],
                    "request_payload_bytes": len(json.dumps(request_body, default=str).encode("utf-8")),
                })
            emit(logger, logging.ERROR, "model_call_failed", service="jev", model=selected_model,
                 prompt_version="v2", **error_fields)
            raise
        if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
            raise RuntimeError("TypeSafe returned an invalid System One response")
        emit(logger, logging.INFO, "model_call_completed", service="jev", model=selected_model,
             prompt_version="v2", response_model=body.get("model"), model_version=body.get("version"),
             questions=sorted(questions), duration_ms=elapsed_ms(started), usage=body.get("usage"))
        return body

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
