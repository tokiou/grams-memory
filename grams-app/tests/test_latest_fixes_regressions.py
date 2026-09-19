import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.agent.nodes.apply_memory_update import make_apply_memory_update
from supervisor.agent.nodes.load_process_context import make_load_process_context
from supervisor.agent.runtime import SupervisorRuntime
from supervisor.inbox import EventInbox, InboxRepository
from supervisor.inbox.model import EventStatus, SupervisorEventInput
from supervisor.platform.sqlite.db import open_connection


def _event(event_id, event_type="TEXT_FINAL", text=None):
    payload = {"type": event_type}
    if text is not None:
        payload["payload"] = {"properties": {"part": {"text": text}}}
    return SupervisorEventInput(
        id=event_id,
        payload=payload,
        type=event_type,
        source_event=None,
        session_id="root",
        root_session_id="root",
    )


def test_event_inbox_fail_batch_requeues_without_masking_original_failure(tmp_path):
    async def scenario():
        connection = await open_connection(tmp_path / "fail-batch.db")
        repository = InboxRepository(connection)
        inbox = EventInbox(repository, lease_seconds=1)
        await inbox.initialize()
        try:
            await inbox.persist(_event("e1"))
            event = (await inbox.claim_pending("root", 1))[0]

            await inbox.fail_batch(
                [(event.id, event.lease_id)],
                "graph failed",
                datetime.now(timezone.utc),
            )

            assert (await repository.get_event("e1")).status is EventStatus.PENDING
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_retry_cohort_remains_claimable_when_batch_limit_is_reduced(tmp_path):
    async def scenario():
        connection = await open_connection(tmp_path / "reduced-limit.db")
        repository = InboxRepository(connection)
        inbox = EventInbox(repository, lease_seconds=1)
        await inbox.initialize()
        try:
            for event_id in ("e1", "e2"):
                await inbox.persist(_event(event_id))
            claimed = await inbox.claim_pending("root", 2)
            claims = [(event.id, event.lease_id) for event in claimed]
            await repository.mark_failed_batch(
                claims,
                "retry",
                datetime.now(timezone.utc),
            )

            retried = await inbox.claim_pending("root", 1)

            assert {event.id for event in retried} == {"e1", "e2"}
            assert len({event.cycle_id for event in retried}) == 1
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_cycle_tagged_memory_is_reused_after_process_context_reload():
    async def scenario():
        class Memory:
            def __init__(self):
                self.memories = []

            async def get_process(self, process_id):
                return {
                    "id": process_id,
                    "project_id": "project-1",
                    "key_id": "key-1",
                    "name": "process_001",
                    "status": "ACTIVE",
                }

            async def get_manifest(self):
                return {"projects": [{
                    "id": "project-1",
                    "keys": [{
                        "id": "key-1",
                        "name": "process_001",
                        "categories": [
                            {"id": "strategy-1", "name": "STRATEGY"},
                            {"id": "evidence-1", "name": "EVIDENCE"},
                            {"id": "summary-1", "name": "SUMMARY"},
                        ],
                    }],
                }]}

            async def search(self, query="", **filters):
                category_id = filters["category_id"]
                return [
                    memory for memory in self.memories
                    if memory["category_id"] == category_id
                ]

            async def create(self, value):
                memory = {"id": f"m{len(self.memories) + 1}", **value}
                self.memories.append(memory)
                return memory

            async def link(self, *args, **kwargs):
                return {"id": "edge-1"}

            async def neighbors(self, memory_id, **filters):
                return {"nodes": [], "edges": []}

            async def list_processes(self, project_id):
                return [{
                    "id": "process-1",
                    "project_id": project_id,
                    "key_id": "key-1",
                    "name": "process_001",
                    "status": "ACTIVE",
                }]

        memory = Memory()
        load_context = make_load_process_context(memory)
        apply_update = make_apply_memory_update(memory)
        claim = [{"id": "event-1", "lease_id": "lease-1", "cycle_id": "cycle-fixed"}]

        initial = await load_context({
            "project_id": "project-1",
            "active_process_id": "process-1",
        })
        await apply_update({
            **initial,
            "claimed_events": claim,
            "proposed_memory_update": {
                "memories": [{
                    "category": "STRATEGY",
                    "title": "Plan A",
                    "content": "Try A",
                    "candidate_ref": "new_1",
                }],
                "relations": [],
            },
        })

        reloaded = await load_context({
            "project_id": "project-1",
            "active_process_id": "process-1",
        })
        with pytest.raises(RuntimeError, match="conflicts with the durable cycle proposal"):
            await apply_update({
                **reloaded,
                "claimed_events": claim,
                "proposed_memory_update": {
                    "memories": [{
                        "category": "STRATEGY",
                        "title": "Reworded plan",
                        "content": "Try A more carefully",
                        "candidate_ref": "new_1",
                    }],
                    "relations": [],
                },
            })

        assert len(memory.memories) == 1

    asyncio.run(scenario())


def test_runtime_waits_for_user_message_before_persisting_durable_objective(tmp_path):
    async def scenario():
        connection = await open_connection(tmp_path / "objective-source.db")
        repository = InboxRepository(connection)
        inbox = EventInbox(repository, lease_seconds=1)
        await inbox.initialize()
        captured = []

        class Graph:
            async def ainvoke(self, state):
                captured.append(state["original_task"])
                claims = [
                    (event["id"], event["lease_id"])
                    for event in state["claimed_events"]
                ]
                await inbox.ack_batch(claims)
                return state

        runtime = SupervisorRuntime(Graph(), inbox)
        try:
            await inbox.persist(_event(
                "assistant",
                event_type="TEXT_FINAL",
                text="Assistant interim answer",
            ))
            await runtime.run_cycle("root", original_task="root")

            await inbox.persist(_event(
                "user",
                event_type="USER_MESSAGE_FINAL",
                text="Actual user objective",
            ))
            await runtime.run_cycle("root", original_task="Actual user objective")

            assert captured[-1] == "Actual user objective"
            assert await inbox.get_objective("root") == "Actual user objective"
        finally:
            await connection.close()

    asyncio.run(scenario())
