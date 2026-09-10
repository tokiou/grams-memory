"""Single-flight runtime that activates LangGraph without interpreting events."""

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging
import time
from typing import Any
import uuid

from ..agent.state import SupervisorState
from ..inbox import EventInbox
from ..observability import bind_correlation, elapsed_ms, emit, graph_trace, monotonic_ns, source_lag_info, trace_summary

logger = logging.getLogger(__name__)


class OperationalStatus:
    STARTING = "STARTING"
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class OperationalSnapshot:
    status: str = OperationalStatus.STARTING
    transitioned_at: datetime | None = None
    active_run_id: str | None = None
    active_batch_size: int = 0
    last_error: str | None = None
    wakeup_pending: bool = False
    claimed: int = 0
    processed: int = 0
    done: int = 0
    retried: int = 0
    failed: int = 0


class SupervisorRuntime:
    def __init__(self, inbox: EventInbox, graph: Any, *, checkpoint_id: str = "grams-supervisor", poll_interval: float = 1.0,
                 tick_interval: float = 300.0, lag_warn_ms: float | None = None) -> None:
        self.inbox = inbox
        self.graph = graph
        self.checkpoint_id = checkpoint_id
        self.poll_interval = poll_interval
        self.tick_interval = tick_interval
        self.lag_warn_ms = lag_warn_ms
        self._wakeup = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self._worker_task: asyncio.Task[None] | None = None
        self._stop_requested = False
        self._active_sessions: dict[str, dict[str, Any]] = {}
        self._snapshot = OperationalSnapshot(transitioned_at=datetime.now(timezone.utc))

    @property
    def snapshot(self) -> OperationalSnapshot:
        return replace(self._snapshot, wakeup_pending=self._wakeup.is_set())

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._worker_task

    def notify(self) -> None:
        if not self._stop_requested:
            coalesced = self._wakeup.is_set()
            self._wakeup.set()
            if not coalesced:
                emit(logger, logging.DEBUG, "runtime_wakeup", coalesced=False)

    async def start(self) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._stop_requested = False
        self._transition(OperationalStatus.STARTING)
        self._worker_task = asyncio.create_task(self._worker(), name="grams-supervisor-runtime")
        self.notify()

    async def run_once(self) -> bool:
        """Run at most one logical graph invocation at a time."""
        if self._stop_requested:
            return False
        async with self._run_lock:
            if self._stop_requested:
                return False
            roots = await self.inbox.claimable_roots()
            supervisor_tick = False
            if roots:
                root_id = roots[0]
                run_id = str(uuid.uuid4())
                claimed = await self.inbox.claim_pending(root_id, run_id=run_id)
                if not claimed:
                    self._transition(OperationalStatus.IDLE)
                    return False
            else:
                now = time.monotonic()
                active = next(
                    (item for item in self._active_sessions.values()
                     if now - item["last_tick"] >= self.tick_interval),
                    None,
                )
                if active is None:
                    self._transition(OperationalStatus.IDLE)
                    return False
                # Recheck the Inbox immediately before a tick so new events win.
                roots = await self.inbox.claimable_roots()
                if roots:
                    root_id = roots[0]
                    run_id = str(uuid.uuid4())
                    claimed = await self.inbox.claim_pending(root_id, run_id=run_id)
                    if not claimed:
                        self._transition(OperationalStatus.IDLE)
                        return False
                else:
                    root_id = active["root_session_id"]
                    run_id = str(uuid.uuid4())
                    claimed = []
                    supervisor_tick = True
                    active["last_tick"] = now
                    emit(logger, logging.INFO, "supervisor_tick_started", run_id=run_id,
                         root_session_id=root_id, thread_id=active["thread_id"])
            claimed_state = [
                {
                    "id": event.id,
                    "session_id": event.session_id,
                    "root_session_id": event.root_session_id,
                    "type": event.type,
                    "source_event": event.source_event,
                    "payload": event.payload,
                    "received_at": event.received_at.isoformat(),
                    "processing_at": event.processing_at.isoformat() if event.processing_at else None,
                    "retry_count": event.retry_count,
                    "source_run_id": event.source_run_id,
                    "ingress_id": event.ingress_id,
                    "lease_id": event.lease_id,
                }
                for event in claimed
            ]
            thread_id = self._active_sessions.get(root_id, {}).get("thread_id") or f"{self.checkpoint_id}:{root_id}"
            self._transition(OperationalStatus.RUNNING, active_run_id=run_id)
            self._snapshot = replace(self._snapshot, active_batch_size=len(claimed), claimed=self._snapshot.claimed + len(claimed))
            claim_started = {event.id: monotonic_ns() for event in claimed}
            emit(logger, logging.INFO, "run_claimed", run_id=run_id, root_session_id=root_id,
                 thread_id=thread_id, batch_size=len(claimed), event_ids=",".join(event.id for event in claimed))
            run_started = monotonic_ns()
            emit(logger, logging.INFO, "run_started", run_id=run_id, root_session_id=root_id,
                 thread_id=thread_id, batch_size=len(claimed))
            graph_started = monotonic_ns()
            try:
                with graph_trace():
                    with bind_correlation(
                        run_id=run_id,
                        root_session_id=root_id,
                        thread_id=thread_id,
                        event_ids=",".join(event.id for event in claimed),
                    ):
                        result: SupervisorState = await self.graph.ainvoke(
                            {
                                "root_session_id": root_id,
                                "supervisor_checkpoint_id": self.checkpoint_id,
                                "run_id": run_id,
                                "claimed_events": claimed_state,
                                "processing_events": [],
                                "supervisor_tick": supervisor_tick,
                            },
                            config={"configurable": {"thread_id": thread_id}},
                        )
                graph_details = trace_summary()
                emit(logger, logging.INFO, "graph_summary", run_id=run_id, root_session_id=root_id,
                     steps=graph_details["steps"], node_counts=graph_details["node_counts"])
                if result.get("session_status") == "active":
                    self._active_sessions[root_id] = {
                        "root_session_id": root_id,
                        "thread_id": thread_id,
                        "last_tick": time.monotonic(),
                    }
                else:
                    self._active_sessions.pop(root_id, None)
                if supervisor_tick:
                    emit(logger, logging.INFO, "supervisor_tick_completed", run_id=run_id,
                         root_session_id=root_id, thread_id=thread_id,
                         next_action=result.get("next_action"), supervisor_tick=True,
                         decision_source=(result.get("assessment") or {}).get("decision_source"))
                processing_events = result.get("processing_events", [])
                processed_ids = {event.get("id") for event in processing_events}
                omitted_count = 0
                for event in claimed_state:
                    if event["id"] not in processed_ids:
                        omitted_count += 1
                        await self.inbox.mark_failed(event["id"], "graph completed without incorporating event", lease_id=event.get("lease_id"))
                for event in processing_events:
                    if not await self.inbox.mark_processed(event["id"], event.get("lease_id")):
                        emit(logger, logging.ERROR, "lease_conflict", event_id=event["id"], run_id=run_id,
                             root_session_id=root_id)
                        raise RuntimeError(f"lease conflict while processing {event['id']}")
                    now = datetime.now(timezone.utc)
                    received_at = datetime.fromisoformat(event["received_at"])
                    processing_at = datetime.fromisoformat(event["processing_at"]) if event.get("processing_at") else None
                    source_lag, source_timestamp_status = source_lag_info(event.get("payload"), now=now)
                    queue_lag = round((processing_at - received_at).total_seconds() * 1000, 1) if processing_at else None
                    ack_fields = dict(event_id=event["id"],
                         session_id=event.get("session_id"), root_session_id=root_id, run_id=run_id,
                          thread_id=thread_id, status="PROCESSED", retry_count=event.get("retry_count"),
                          ingress_id=event.get("ingress_id"), source_run_id=event.get("source_run_id"), queue_lag_ms=queue_lag,
                          event_duration_ms=round(max(0.0, queue_lag or 0.0) + elapsed_ms(claim_started[event["id"]]), 1),
                          source_lag_ms=source_lag, source_timestamp_status=source_timestamp_status)
                    emit(logger, logging.INFO, "event_acknowledged", **ack_fields)
                    if self.lag_warn_ms is not None:
                        for lag_class, measured in (("queue_lag_ms", queue_lag), ("source_lag_ms", source_lag)):
                            if measured is not None and measured > self.lag_warn_ms:
                                emit(logger, logging.WARNING, "lag_detected", lag_class=lag_class,
                                     measured_ms=measured, threshold_ms=self.lag_warn_ms, **ack_fields)
                outcome = "partial" if omitted_count else "processed"
                self._snapshot = replace(self._snapshot, active_batch_size=len(processing_events), processed=self._snapshot.processed + len(processing_events), done=self._snapshot.done + len(processing_events))
                emit(logger, logging.INFO, "run_completed", run_id=run_id, root_session_id=root_id,
                     thread_id=thread_id, outcome=outcome, processed=len(processing_events), omitted=omitted_count,
                     run_duration_ms=elapsed_ms(graph_started), graph_duration_ms=elapsed_ms(graph_started),
                     run_total_duration_ms=elapsed_ms(run_started))
                self._transition(OperationalStatus.IDLE)
                return not omitted_count
            except asyncio.CancelledError:
                emit(logger, logging.WARNING, "run_cancelled", root_session_id=root_id, run_id=run_id,
                     thread_id=thread_id, graph_duration_ms=elapsed_ms(graph_started),
                     run_total_duration_ms=elapsed_ms(run_started))
                raise
            except Exception as error:
                retried_count = 0
                failed_count = 0
                for event in claimed:
                    await self.inbox.mark_failed(event.id, str(error), lease_id=event.lease_id)
                    if event.retry_count >= self.inbox.repository.max_attempts:
                        failed_count += 1
                    else:
                        retried_count += 1
                self._snapshot = replace(self._snapshot, last_error=str(error), retried=self._snapshot.retried + retried_count, failed=self._snapshot.failed + failed_count)
                self._transition(OperationalStatus.DEGRADED, last_error=str(error))
                emit(logger, logging.ERROR, "runtime_error", category="graph", root_session_id=root_id,
                     run_id=run_id, thread_id=thread_id, error=type(error).__name__,
                     detail=str(error), graph_duration_ms=elapsed_ms(graph_started),
                     run_total_duration_ms=elapsed_ms(run_started))
                return False
            finally:
                self._snapshot = replace(self._snapshot, active_run_id=None, active_batch_size=0)

    async def _worker(self) -> None:
        while not self._stop_requested:
            try:
                if not await self.inbox.has_work():
                    self._wakeup.clear()
                    if await self.inbox.has_work():
                        continue
                    await asyncio.wait_for(self._wakeup.wait(), timeout=self.poll_interval)
                    continue
                self._wakeup.clear()
                await self.run_once()
            except asyncio.TimeoutError:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._transition(OperationalStatus.DEGRADED, last_error=str(error))
                emit(logger, logging.ERROR, "runtime_error", category="worker_cycle", error=type(error).__name__)
                await asyncio.sleep(self.poll_interval)

    async def stop(self, timeout: float = 5.0) -> None:
        if self._worker_task is None:
            self._transition(OperationalStatus.STOPPED)
            return
        self._stop_requested = True
        self._transition(OperationalStatus.STOPPING)
        self._wakeup.set()
        try:
            await asyncio.wait_for(self._worker_task, timeout)
        except asyncio.TimeoutError:
            self._worker_task.cancel()
            await asyncio.gather(self._worker_task, return_exceptions=True)
            emit(logger, logging.WARNING, "runtime_shutdown_timeout", timeout_seconds=timeout)
        finally:
            self._worker_task = None
            self._active_sessions.clear()
            self._transition(OperationalStatus.STOPPED)

    def _transition(self, status: str, *, active_run_id: str | None = None, last_error: str | None = None) -> None:
        previous = self._snapshot.status
        self._snapshot = replace(
            self._snapshot,
            status=status,
            transitioned_at=datetime.now(timezone.utc),
            active_run_id=active_run_id if status == OperationalStatus.RUNNING else self._snapshot.active_run_id,
            last_error=last_error if last_error is not None else self._snapshot.last_error,
        )
        if previous != status:
            emit(logger, logging.INFO, "runtime_status_changed", previous=previous, status=status,
                 active_run_id=active_run_id, last_error=last_error)
