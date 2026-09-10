"""Durable Inbox plus an in-memory wakeup signal."""

import asyncio
from datetime import datetime, timezone
import logging
import sqlite3

from .model import SupervisorEvent, SupervisorEventInput
from .repository import InboxRepository
from ..observability import elapsed_ms, emit, event_correlation, monotonic_ns, source_lag_info

logger = logging.getLogger(__name__)


class EventInbox:
    def __init__(self, repository: InboxRepository, *, batch_size: int = 20, lease_seconds: float = 60.0, lag_warn_ms: float | None = None) -> None:
        self.repository = repository
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self.lag_warn_ms = lag_warn_ms
        self._work = asyncio.Event()

    async def initialize(self) -> None:
        await self.repository.initialize()
        await self.repository.recover_unfinished()
        if await self.repository.count_pending():
            self._work.set()

    async def persist(self, event: SupervisorEventInput) -> str:
        started = monotonic_ns()
        event_id = await self._retry_locked(lambda: self.repository.insert_event(event))
        self._work.set()
        emit(logger, logging.INFO, "event_persisted", event_id=event_id, session_id=event.session_id,
             root_session_id=event.root_session_id, event_type=event.type,
             ingress_id=event.ingress_id, persist_duration_ms=elapsed_ms(started))
        return event_id

    async def insert(self, event: SupervisorEventInput) -> str:
        return await self.persist(event)

    async def claim_batch(self, root_session_id: str, limit: int | None = None, lease_seconds: float | None = None, *, run_id: str | None = None) -> list[SupervisorEvent]:
        events = await self._retry_locked(
            lambda: self.repository.claim_pending(
                root_session_id,
                limit or self.batch_size,
                lease_seconds or self.lease_seconds,
                run_id=run_id,
            )
        )
        return events

    async def ack(self, event_id: str, lease_id: str | None = None) -> bool:
        return await self.mark_processed(event_id, lease_id)

    async def fail(self, event_id: str, error: str, retry_at: datetime | None = None, lease_id: str | None = None) -> None:
        await self.mark_failed(event_id, error, retry_at, lease_id)

    async def pending_count(self, root_session_id: str | None = None) -> int:
        return await self.repository.count_pending(root_session_id)

    async def peek_pending(self, root_session_id: str | None = None, limit: int | None = None) -> list[SupervisorEvent]:
        return await self.repository.list_pending(root_session_id, limit)

    async def read_pending(self, root_session_id: str, limit: int | None = None, *, run_id: str | None = None) -> list[SupervisorEvent]:
        return await self.repository.list_pending(root_session_id, limit or self.batch_size)

    async def claim_pending(self, root_session_id: str, limit: int | None = None, *, run_id: str | None = None) -> list[SupervisorEvent]:
        events = await self._retry_locked(lambda: self.repository.claim_pending(root_session_id, limit or self.batch_size, self.lease_seconds, run_id=run_id))
        for event in events:
            queue_lag = max(0.0, (event.processing_at - event.received_at).total_seconds() * 1000) if event.processing_at else None
            source_lag, source_timestamp_status = source_lag_info(event.payload, now=event.processing_at)
            fields = {**event_correlation(event), "run_id": run_id, "queue_lag_ms": round(queue_lag, 1) if queue_lag is not None else None, "source_lag_ms": source_lag}
            fields["source_timestamp_status"] = source_timestamp_status
            emit(logger, logging.INFO, "event_claimed", **fields)
            if self.lag_warn_ms is not None and queue_lag is not None and queue_lag > self.lag_warn_ms:
                emit(logger, logging.WARNING, "lag_detected", lag_class="queue_lag_ms", measured_ms=round(queue_lag, 1), threshold_ms=self.lag_warn_ms, **fields)
            if self.lag_warn_ms is not None and source_lag is not None and source_lag > self.lag_warn_ms:
                emit(logger, logging.WARNING, "lag_detected", lag_class="source_lag_ms", measured_ms=source_lag, threshold_ms=self.lag_warn_ms, **fields)
        return events

    async def mark_processing(self, event_id: str, *, run_id: str | None = None) -> bool:
        return await self.repository.mark_processing(event_id, run_id=run_id)

    async def mark_processed(self, event_id: str, lease_id: str | None = None) -> bool:
        result = await self._retry_locked(lambda: self.repository.mark_processed(event_id, lease_id))
        return result

    async def mark_failed(self, event_id: str, error: str, retry_at: datetime | None = None, lease_id: str | None = None) -> None:
        before = await self.repository.get_event(event_id)
        status = await self._retry_locked(lambda: self.repository.mark_failed(event_id, error, retry_at, lease_id))
        after = await self.repository.get_event(event_id)
        if before and status == before.status and before.lease_id != lease_id:
            emit(logger, logging.ERROR, "lease_conflict", **event_correlation(before), error="lease mismatch")
            return
        emit(logger, logging.WARNING if status and status.value == "PENDING" else logging.ERROR,
             "event_retry_scheduled" if status and status.value == "PENDING" else "event_failed",
             **event_correlation(after or before), status=status.value if status else None,
             error=error)
        if status and status.value == "PENDING":
            self._work.set()

    async def pending_roots(self) -> list[str]:
        return await self.repository.pending_roots()

    async def claimable_roots(self) -> list[str]:
        return await self.repository.claimable_roots()

    async def fail_processing_for_root(self, root_session_id: str, error: str) -> None:
        for event in await self.repository.processing_for_root(root_session_id):
            await self.mark_failed(event.id, error, lease_id=event.lease_id)

    async def wait_for_work(self) -> None:
        await self._work.wait()

    async def has_pending(self) -> bool:
        return await self.pending_count() > 0

    async def has_work(self) -> bool:
        return bool(await self.claimable_roots())

    async def has_ready(self) -> bool:
        return await self.has_work()

    async def recover_expired(self, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        unfinished = await self.repository.list_unfinished()
        expired = [event for event in unfinished if event.status.value == "PROCESSING" and (event.lease_until is None or event.lease_until <= current)]
        await self.repository.recover_expired(current)
        for event in expired:
            emit(logger, logging.WARNING, "lease_recovered", **event_correlation(event),
                 previous_status=event.status.value)
        if await self.has_work():
            self._work.set()

    async def close(self) -> None:
        self._work.set()

    @staticmethod
    async def _retry_locked(operation):
        for attempt in range(60):
            try:
                return await operation()
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() or attempt == 59:
                    raise
                await asyncio.sleep(min(0.2, 0.01 * (attempt + 1)))
