"""OpenRouter service for generative Supervisor models."""

from __future__ import annotations

from typing import Any
import os

import httpx


class OpenRouterService:
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
        model: str | None = None,
        temperature: float = 0.0,
        response_format: dict[str, Any] | None = None,
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
        response = await self._http.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("OpenRouter returned an invalid response")
        return body

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
