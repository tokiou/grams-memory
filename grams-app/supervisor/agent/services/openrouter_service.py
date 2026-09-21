"""OpenRouter service for generative Supervisor models."""

from __future__ import annotations

from typing import Any
import json
import logging
import os

import httpx

from supervisor.observability import elapsed_ms, emit, monotonic_ns

logger = logging.getLogger(__name__)

class OpenRouterClient:
    """Small async OpenAI-compatible client for text generation."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.base_url = (base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")).rstrip("/")
        self.model = model or os.getenv("OPENROUTER_DEPLOYMENT", "")
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        operation: str = "chat",
        model: str | None = None,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required")
        selected_model = model or self.model
        if not selected_model:
            raise RuntimeError("OPENROUTER_DEPLOYMENT is required")
        payload: dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if max_tokens is not None:
            if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
                raise ValueError("max_tokens must be a positive integer")
            payload["max_tokens"] = max_tokens
        payload["reasoning"] = {"enabled": False}
        started = monotonic_ns()
        emit(logger, logging.INFO, "model_call_started", service="openrouter", model=selected_model,
             operation=operation, prompt_version="v2")
        try:
            response = await self._http.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as error:
            emit(logger, logging.ERROR, "model_call_failed", service="openrouter", model=selected_model,
                 operation=operation, prompt_version="v2", error=type(error).__name__, duration_ms=elapsed_ms(started))
            raise
        if not isinstance(body, dict):
            raise RuntimeError("OpenRouter returned an invalid response")
        emit(logger, logging.INFO, "model_call_completed", service="openrouter", model=selected_model,
             response_model=body.get("model"), operation=operation, prompt_version="v2",
             duration_ms=elapsed_ms(started), usage=body.get("usage"))
        return body

    @staticmethod
    def _content(body: dict[str, Any]) -> str:
        choices = body.get("choices", [])
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("OpenRouter returned no choices")
        choice = choices[0]
        if isinstance(choice, dict) and choice.get("finish_reason") == "length":
            raise RuntimeError("OpenRouter response was truncated by max_tokens")
        content = choice.get("message", {}).get("content") if isinstance(choice, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("OpenRouter returned empty content")
        return content

    async def generate_json(
        self,
        *,
        operation: str,
        payload: dict[str, Any],
        system_prompt: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        body = await self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=True, sort_keys=True)},
            ],
            operation=operation,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": operation.lower(), "strict": True, "schema": schema},
            },
        )
        try:
            value = json.loads(self._content(body))
        except json.JSONDecodeError as error:
            raise RuntimeError("OpenRouter returned invalid JSON content") from error
        if not isinstance(value, dict):
            raise RuntimeError("OpenRouter JSON content must be an object")
        return value

    async def generate_text(
        self,
        *,
        operation: str,
        payload: dict[str, Any],
        system_prompt: str,
    ) -> str:
        body = await self.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=True, sort_keys=True)},
            ],
            operation=operation,
        )
        return self._content(body).strip()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
