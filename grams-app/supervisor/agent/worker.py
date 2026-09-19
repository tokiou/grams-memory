"""Background consumer that drains durable Inbox roots through SupervisorRuntime."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from supervisor.observability import emit

logger = logging.getLogger(__name__)


def _nested_text(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("text", "content"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
        for item in value.values():
            if text := _nested_text(item):
                return text
    elif isinstance(value, list):
        for item in value:
            if text := _nested_text(item):
                return text
    return None


def _first_text(value: Any) -> str | None:
    if isinstance(value, dict):
        role = value.get("role")
        if not role and isinstance(value.get("info"), dict):
            role = value["info"].get("role")
        if role == "user":
            return _nested_text(value.get("parts") or value.get("content") or value)
        for item in value.values():
            if text := _first_text(item):
                return text
    elif isinstance(value, list):
        for item in value:
            if text := _first_text(item):
                return text
    return None


class SupervisorWorker:
    def __init__(self, runtime, inbox, opencode, *, poll_seconds: float = 0.25) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.runtime = runtime
        self.inbox = inbox
        self.opencode = opencode
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._tasks: dict[str, str] = {}

    async def _task_for(self, root_session_id: str) -> str:
        if root_session_id not in self._tasks:
            try:
                context = await self.opencode.get_context(root_session_id)
            except Exception as error:
                emit(
                    logger,
                    logging.WARNING,
                    "opencode_task_context_unavailable",
                    root_session_id=root_session_id,
                    error=type(error).__name__,
                )
                return root_session_id
            self._tasks[root_session_id] = _first_text(context) or root_session_id
        return self._tasks[root_session_id]

    async def run_forever(self) -> None:
        emit(logger, logging.INFO, "supervisor_worker_started")
        try:
            while not self._stop.is_set():
                roots = await self.inbox.claimable_roots()
                if not roots:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
                    except TimeoutError:
                        pass
                    continue
                for root_session_id in roots:
                    if self._stop.is_set():
                        break
                    try:
                        result = await self.runtime.run_cycle(
                            root_session_id,
                            original_task=await self._task_for(root_session_id),
                        )
                        if result.get("final_status") == "NO_EVENTS":
                            await asyncio.sleep(self.poll_seconds)
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        emit(
                            logger,
                            logging.ERROR,
                            "supervisor_cycle_failed",
                            root_session_id=root_session_id,
                            error=type(error).__name__,
                            detail=str(error)[:500],
                        )
                        await asyncio.sleep(self.poll_seconds)
        finally:
            emit(logger, logging.INFO, "supervisor_worker_stopped")

    def stop(self) -> None:
        self._stop.set()
