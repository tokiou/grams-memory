"""Explicit Supervisor cycle runner; the event receiver does not start it."""

from __future__ import annotations

import asyncio
from typing import Any
import uuid

from supervisor.agent.graph import build_graph
from supervisor.agent.nodes.read_inbox import make_read_inbox
from supervisor.agent.state_builder import normalize_event
from supervisor.observability import bind_correlation, graph_trace


class SupervisorRuntime:
    def __init__(
        self,
        graph,
        inbox,
        *,
        batch_size: int = 20,
        run_id: str | None = None,
        lease_heartbeat_seconds: float | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.graph = graph
        self.inbox = inbox
        self._read = make_read_inbox(inbox, batch_size=batch_size, run_id=run_id)
        lease_seconds = float(getattr(inbox, "lease_seconds", 60.0))
        heartbeat = lease_heartbeat_seconds if lease_heartbeat_seconds is not None else lease_seconds / 3
        if heartbeat <= 0 or heartbeat >= lease_seconds:
            raise ValueError("lease heartbeat must be positive and shorter than the Inbox lease")
        self.lease_heartbeat_seconds = heartbeat

    async def _heartbeat(self, claimed: list[dict[str, Any]]) -> None:
        while True:
            await asyncio.sleep(self.lease_heartbeat_seconds)
            claims = [(event["id"], event["lease_id"]) for event in claimed]
            if hasattr(self.inbox, "renew_leases"):
                renewed = await self.inbox.renew_leases(claims)
            else:
                renewed = all(await asyncio.gather(*(
                    self.inbox.renew_lease(event_id, lease_id)
                    for event_id, lease_id in claims
                )))
            if not renewed:
                raise RuntimeError("lost Inbox lease while processing Supervisor cycle")

    async def _fail_claimed(self, claimed: list[dict[str, Any]], error: BaseException) -> None:
        detail = f"{type(error).__name__}: {error}"[:1000]
        if hasattr(self.inbox, "fail_batch"):
            await self.inbox.fail_batch(
                [(event["id"], event["lease_id"]) for event in claimed],
                detail,
            )
            return
        await asyncio.gather(*(
            self.inbox.fail(event["id"], detail, lease_id=event["lease_id"])
            for event in claimed
        ), return_exceptions=True)

    async def run_cycle(self, root_session_id: str, **initial_state: Any) -> dict[str, Any]:
        if not isinstance(root_session_id, str) or not root_session_id:
            raise ValueError("root_session_id is required")
        state = {**initial_state, "root_session_id": root_session_id}
        claimed = (await self._read(state))["claimed_events"]
        if not claimed:
            return {**state, "claimed_events": [], "final_status": "NO_EVENTS"}
        state["claimed_events"] = claimed
        graph_task: asyncio.Task | None = None
        heartbeat_task: asyncio.Task | None = (
            asyncio.create_task(self._heartbeat(claimed))
            if hasattr(self.inbox, "renew_leases") or hasattr(self.inbox, "renew_lease")
            else None
        )
        try:
            durable_objective = (
                await self.inbox.get_objective(root_session_id)
                if hasattr(self.inbox, "get_objective")
                else None
            )
            persist_objective = False
            if durable_objective:
                state["original_task"] = durable_objective
            else:
                normalized_events = [normalize_event(event) for event in claimed]
                user_task = next(
                    (
                        event.get("text")
                        for event in normalized_events
                        if event.get("type") == "USER_MESSAGE_FINAL" and event.get("text")
                    ),
                    None,
                )
                fallback_text = next(
                    (event.get("text") for event in normalized_events if event.get("text")),
                    None,
                )
                supplied = state.get("original_task")
                supplied_objective = supplied if supplied and supplied != root_session_id else None
                state["original_task"] = user_task or supplied_objective or fallback_text or root_session_id
                persist_objective = bool(user_task or supplied_objective)
            if persist_objective and hasattr(self.inbox, "set_objective"):
                state["original_task"] = await self.inbox.set_objective(
                    root_session_id,
                    state["original_task"],
                )
            if heartbeat_task is not None and heartbeat_task.done():
                return await heartbeat_task

            async def invoke_graph():
                with bind_correlation(
                    call_id=str(uuid.uuid4()),
                    root_session_id=root_session_id,
                    event_ids=[event["id"] for event in claimed],
                ), graph_trace():
                    return await self.graph.ainvoke(state)

            graph_task = asyncio.create_task(invoke_graph())
            if heartbeat_task is None:
                return await graph_task
            done, _ = await asyncio.wait(
                {graph_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if graph_task in done:
                return await graph_task
            if heartbeat_task in done:
                graph_task.cancel()
                await asyncio.gather(graph_task, return_exceptions=True)
                return await heartbeat_task
            raise RuntimeError("Supervisor cycle tasks stopped unexpectedly")
        except asyncio.CancelledError as error:
            for task in (graph_task, heartbeat_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (graph_task, heartbeat_task) if task is not None),
                return_exceptions=True,
            )
            await asyncio.shield(self._fail_claimed(claimed, error))
            raise
        except Exception as error:
            await self._fail_claimed(claimed, error)
            raise
        finally:
            tasks = [task for task in (graph_task, heartbeat_task) if task is not None]
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)


def build_runtime(
    *,
    inbox,
    memory,
    jev,
    openrouter,
    opencode,
    process_service=None,
    batch_size=20,
    run_id=None,
    max_expansion_depth=3,
    checkpointer=None,
) -> SupervisorRuntime:
    graph = build_graph(
        inbox=inbox,
        memory=memory,
        jev=jev,
        openrouter=openrouter,
        opencode=opencode,
        process_service=process_service,
        batch_size=batch_size,
        run_id=run_id,
        max_expansion_depth=max_expansion_depth,
        checkpointer=checkpointer,
    )
    return SupervisorRuntime(graph, inbox, batch_size=batch_size, run_id=run_id)
