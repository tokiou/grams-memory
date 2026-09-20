"""TypeSafe Jev service for structured Supervisor decisions."""

from __future__ import annotations

import json
from typing import Any
import logging
import os

import httpx

from supervisor.observability import elapsed_ms, emit, monotonic_ns
from supervisor.agent.state_builder import (
    build_jev_process_state,
    compact_jev_state,
    estimate_json_tokens,
)

logger = logging.getLogger(__name__)

class JevClient:
    """Async client for TypeSafe System One evaluations."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_context_tokens: int | None = None,
        timeout: float = 30.0,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY", "")
        self.base_url = (base_url or os.getenv("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1")).rstrip("/")
        self.model = model or os.getenv("TYPESAFE_MODEL", "jev-latest")
        # Keep the complete serialized request below the documented 32k context
        # limit. The tokenizer is not published, so UTF-8 bytes are a
        # conservative upper bound for token count.
        self.max_context_tokens = max_context_tokens if max_context_tokens is not None else int(
            os.getenv("TYPESAFE_CONTEXT_MAX_TOKENS", "32768")
        )
        if (
            not isinstance(self.max_context_tokens, int)
            or isinstance(self.max_context_tokens, bool)
            or self.max_context_tokens <= 0
        ):
            raise ValueError("max_context_tokens must be a positive integer")
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
        state = build_jev_process_state(state) if isinstance(state, dict) and "process_context" in state else state
        request_body = {"model": selected_model, "state": state, "questions": questions}
        request_without_state = {
            "model": selected_model,
            "state": {},
            "questions": questions,
        }
        request_overhead = estimate_json_tokens(request_without_state) - 2
        state_limit = self.max_context_tokens - request_overhead
        if state_limit <= 0:
            raise ValueError("Jev questions cannot fit the configured request byte limit")
        compacted_state = compact_jev_state(state, max_tokens=state_limit)
        request_body["state"] = compacted_state
        serialized_request = json.dumps(
            request_body, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        state_bytes = estimate_json_tokens(compacted_state)
        request_bytes = len(serialized_request)
        if request_bytes > self.max_context_tokens:
            raise ValueError("Jev context cannot fit configured request byte limit after compaction")
        emit(logger, logging.INFO, "model_call_started", service="jev", model=selected_model,
             prompt_version="v2", questions=sorted(questions), question_count=len(questions),
             context_budget_bytes=self.max_context_tokens,
             state_bytes=state_bytes, request_bytes=request_bytes,
             context_compacted=bool(compacted_state.get("context_compaction")))
        try:
            response = await self._http.post(
                f"{self.base_url}/systemone",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                content=serialized_request,
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
                    "request_payload_bytes": len(serialized_request),
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
