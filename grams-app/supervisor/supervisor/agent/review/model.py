"""OpenAI-compatible model client used by REVIEW."""

from typing import Any
import json
import logging

import httpx

from ...observability import elapsed_ms, emit, monotonic_ns
from ..nodes.memory_operation import OPERATIONS
from ..prompts import REVIEW_PROMPT
from .schema import ReviewDecisionError, ReviewModel, VALID_ACTIONS

logger = logging.getLogger(__name__)


class OpenAIReviewModel:
    """Calls an OpenAI-compatible chat completion endpoint for REVIEW."""

    def __init__(self, base_url: str, api_key: str | None, model: str, *, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._http = httpx.AsyncClient(timeout=timeout)

    async def _completion(self, context: dict[str, Any]) -> httpx.Response:
        started = monotonic_ns()
        operation = f"chat.completions:{self.model}"
        emit(logger, logging.DEBUG, "external_call_started", service="model", operation=operation,
             context_event_count=len(context.get("recent_events", [])))
        try:
            response = await self._http.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0,
                    "reasoning": {"effort": "none"},
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": REVIEW_PROMPT + " Return exactly one JSON object matching the response contract above; do not include markdown or extra text."},
                        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                    ],
                },
            )
            response.raise_for_status()
        except Exception as error:
            emit(logger, logging.ERROR, "external_call_failed", service="model", operation=operation,
                 outcome="error", error=type(error).__name__, duration_ms=elapsed_ms(started))
            raise
        emit(logger, logging.DEBUG, "external_call_completed", service="model", operation=operation,
             outcome="ok", status_code=response.status_code, duration_ms=elapsed_ms(started))
        return response

    async def decide(self, context: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for Supervisor REVIEW")
        response = await self._completion(context)
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        text = str(content).strip()
        candidates = [text]
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            candidates.append(text[start : end + 1])
        decision = None
        for candidate in candidates:
            try:
                parsed = json.loads(candidate.strip().strip("`").removeprefix("json").strip())
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                decision = parsed
                break
        if decision is None or not isinstance(decision.get("action"), str):
            raise ReviewDecisionError("Supervisor model returned invalid REVIEW JSON")
        action = decision["action"].upper()
        if action not in VALID_ACTIONS:
            raise ReviewDecisionError(f"Supervisor model returned unsupported REVIEW action: {action}")
        if action == "MEMORY_OPERATION":
            operation = decision.get("operation")
            if not isinstance(operation, str) or operation.lower() not in OPERATIONS:
                raise ReviewDecisionError("Supervisor model returned an invalid memory operation")
            if not isinstance(decision.get("arguments", {}), dict):
                raise ReviewDecisionError("Supervisor model returned invalid memory arguments")
        if action == "INTERVENE":
            intervention = decision.get("intervention") or decision.get("arguments")
            if not isinstance(intervention, dict) or intervention.get("action", "message") not in {"message", "abort", "task_control"}:
                raise ReviewDecisionError("Supervisor model returned an invalid intervention")
        decision["action"] = action
        return decision

    async def aclose(self) -> None:
        await self._http.aclose()

    async def healthcheck(self) -> None:
        started = monotonic_ns()
        operation = "GET /models"
        emit(logger, logging.DEBUG, "external_call_started", service="model", operation=operation)
        try:
            response = await self._http.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                raise RuntimeError("model endpoint returned an invalid models response")
            model_ids = {item.get("id") for item in body["data"] if isinstance(item, dict)}
            if model_ids and self.model not in model_ids:
                raise RuntimeError(f"configured model is unavailable: {self.model}")
        except Exception as error:
            emit(logger, logging.ERROR, "external_call_failed", service="model", operation=operation,
                 error=type(error).__name__, detail=str(error), duration_ms=elapsed_ms(started))
            raise
        emit(logger, logging.DEBUG, "external_call_completed", service="model", operation=operation,
             outcome="ok", status_code=response.status_code, duration_ms=elapsed_ms(started))
