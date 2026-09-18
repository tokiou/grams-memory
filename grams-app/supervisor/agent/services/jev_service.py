"""TypeSafe Jev service for structured Supervisor decisions."""

from __future__ import annotations

from typing import Any
import os

import httpx

class JevService:
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
        response = await self._http.post(
            f"{self.base_url}/systemone",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": model or self.model, "state": state, "questions": questions},
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
            raise RuntimeError("TypeSafe returned an invalid System One response")
        return body

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
