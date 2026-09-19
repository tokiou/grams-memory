import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.agent.nodes.send_intervention import make_send_intervention
from supervisor.agent.nodes.common import cycle_key
from supervisor.agent.nodes.expand_graph import make_expand_graph
from supervisor.agent.runtime import SupervisorRuntime
from supervisor.agent.services.process_service import ProcessService
from supervisor.agent.worker import SupervisorWorker
from supervisor.inbox import EventInbox, InboxRepository
from supervisor.inbox.model import EventStatus, SupervisorEventInput
from supervisor.platform.sqlite.db import open_connection


def test_atomic_batch_ack_rejects_stale_leases_without_partial_updates(tmp_path):
    async def scenario():
        connection = await open_connection(tmp_path / "inbox.db")
        repository = InboxRepository(connection)
        inbox = EventInbox(repository)
        await inbox.initialize()
        try:
            for event_id in ("e1", "e2"):
                await inbox.persist(SupervisorEventInput(
                    id=event_id,
                    payload={"type": "TEXT_FINAL"},
                    type="TEXT_FINAL",
                    source_event=None,
                    session_id="root",
                    root_session_id="root",
                ))
            claimed = await inbox.claim_pending("root", 2)
            claims = [(event.id, event.lease_id) for event in claimed]
            assert await inbox.ack_batch([claims[0], (claims[1][0], "stale")]) is False
            statuses = [(await repository.get_event(event_id)).status for event_id in ("e1", "e2")]
            assert statuses == [EventStatus.PROCESSING, EventStatus.PROCESSING]
            assert await inbox.ack_batch(claims) is True
            assert await inbox.ack_batch(claims) is True
            assert await inbox.ack_batch([(claims[0][0], "stale")]) is False
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_retry_cycles_remain_separate_when_an_older_event_becomes_available(tmp_path):
    async def scenario():
        connection = await open_connection(tmp_path / "cycles.db")
        repository = InboxRepository(connection)
        inbox = EventInbox(repository, lease_seconds=1)
        await inbox.initialize()
        try:
            await inbox.persist(SupervisorEventInput(
                id="older", payload={}, type="TEXT_FINAL", source_event=None,
                session_id="root", root_session_id="root",
            ))
            older = (await inbox.claim_pending("root", 10))[0]
            retry_at = datetime.now(timezone.utc) + timedelta(seconds=0.03)
            await inbox.fail("older", "retry", retry_at, older.lease_id)
            await inbox.persist(SupervisorEventInput(
                id="current", payload={}, type="TEXT_FINAL", source_event=None,
                session_id="root", root_session_id="root",
            ))
            current = (await inbox.claim_pending("root", 10))[0]
            original_cycle = current.cycle_id
            await inbox.fail("current", "retry", retry_at, current.lease_id)
            await asyncio.sleep(0.04)
            first_retry = await inbox.claim_pending("root", 10)
            assert len({event.cycle_id for event in first_retry}) == 1
            assert all(event.id == "older" for event in first_retry)
            await inbox.ack_batch([(event.id, event.lease_id) for event in first_retry])
            reclaimed = (await inbox.claim_pending("root", 10))[0]
            assert reclaimed.cycle_id == original_cycle
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_active_cohort_blocks_concurrent_claims_for_the_same_root(tmp_path):
    async def scenario():
        connection = await open_connection(tmp_path / "root-lock.db")
        repository = InboxRepository(connection)
        inbox = EventInbox(repository, lease_seconds=1)
        await inbox.initialize()
        try:
            for event_id in ("e1", "e2"):
                await inbox.persist(SupervisorEventInput(
                    id=event_id, payload={}, type="TEXT_FINAL", source_event=None,
                    session_id="root", root_session_id="root",
                ))
            active = await inbox.claim_pending("root", 1)
            assert len(active) == 1
            assert await inbox.claimable_roots() == []
            assert await inbox.claim_pending("root", 1) == []
            await inbox.ack_batch([(active[0].id, active[0].lease_id)])
            assert await inbox.claimable_roots() == ["root"]
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_intervention_retry_reconciles_ambiguous_delivery_without_resending():
    async def scenario():
        class Memory:
            def __init__(self):
                self.record = None

            async def search(self, **kwargs):
                return [self.record] if self.record else []

            async def create(self, value):
                self.record = {"id": "audit-1", **value}
                return self.record

            async def update(self, memory_id, value):
                self.record.update(value)
                return self.record

        class OpenCode:
            def __init__(self):
                self.messages = []

            async def send_message(self, session_id, message):
                self.messages.append(message)
                raise RuntimeError("response lost")

            async def get_context(self, session_id):
                return {"messages": [{"info": {"role": "user"}, "parts": [{"text": self.messages[0]}]}]}

        state = {
            "root_session_id": "root",
            "claimed_events": [{"id": "event-1", "lease_id": "lease-1"}],
            "intervention_message": "Reconsider the failed strategy.",
            "process_context": {"key": {"evidence_category_id": "evidence"}},
        }
        memory = Memory()
        opencode = OpenCode()
        node = make_send_intervention(opencode, memory)
        with pytest.raises(RuntimeError, match="response lost"):
            await node(state)
        retry_state = {**state, "intervention_message": "A newly generated message."}
        result = await node(retry_state)
        assert len(opencode.messages) == 1
        assert result["intervention_result"]["delivered"] is True
        assert result["intervention_result"]["message"] == "Reconsider the failed strategy."
        assert memory.record["content"].startswith("DELIVERED\n")
        assert "newly generated" not in memory.record["content"]

    asyncio.run(scenario())


def test_ambiguous_invisible_intervention_is_not_resent():
    async def scenario():
        class Memory:
            def __init__(self):
                self.record = None

            async def search(self, **kwargs):
                return [self.record] if self.record else []

            async def create(self, value):
                self.record = {"id": "audit-1", **value}
                return self.record

            async def update(self, memory_id, value):
                self.record.update(value)
                return self.record

        class OpenCode:
            def __init__(self):
                self.send_count = 0

            async def send_message(self, session_id, message):
                self.send_count += 1
                raise RuntimeError("response lost")

            async def get_context(self, session_id):
                return {"messages": []}

        state = {
            "root_session_id": "root",
            "claimed_events": [{"id": "event-1", "lease_id": "lease-1"}],
            "intervention_message": "Durable warning.",
            "process_context": {"key": {"evidence_category_id": "evidence"}},
        }
        memory = Memory()
        opencode = OpenCode()
        node = make_send_intervention(opencode, memory)
        with pytest.raises(RuntimeError, match="response lost"):
            await node(state)
        result = await node(state)
        assert opencode.send_count == 1
        assert result["intervention_result"]["delivery_status"] == "UNKNOWN"
        assert memory.record["content"].startswith("DELIVERY_UNKNOWN\n")

    asyncio.run(scenario())


def test_process_service_recovers_superseded_transition_and_terminal_retry():
    async def scenario():
        class Memory:
            def __init__(self, status):
                self.status = status
                self.created = []

            async def get_active_process(self, project_id):
                return None

            async def list_processes(self, project_id):
                return [{
                    "id": "p1", "project_id": project_id, "key_id": "k1",
                    "name": "process_001", "status": self.status,
                }, *self.created]

            async def get_process(self, process_id):
                return next(item for item in await self.list_processes("project") if item["id"] == process_id)

            async def create_process(self, value):
                process = {
                    "id": "p2", "project_id": value["project_id"], "key_id": "k2",
                    "name": value["name"], "status": "ACTIVE",
                    "predecessor_id": value.get("predecessor_id"),
                }
                self.created.append(process)
                return process

            async def search(self, query="", **kwargs):
                return [{"title": f"Process summary [{query}]: SUCCEEDED"}] if self.status == "SUCCEEDED" else []

        superseded = await ProcessService(Memory("SUPERSEDED")).ensure_active("project", "cycle-1")
        assert superseded["name"] == "process_002"
        assert superseded["recovered_transition"] is True

        terminal_memory = Memory("SUCCEEDED")
        terminal = await ProcessService(terminal_memory).ensure_active("project", "cycle-1")
        assert terminal["id"] == "p1"
        assert terminal["cycle_complete"] is True
        assert terminal_memory.created == []

        class ActiveMemory(Memory):
            async def get_active_process(self, project_id):
                return {
                    "id": "p1", "project_id": project_id, "key_id": "k1",
                    "name": "process_001", "status": "ACTIVE",
                }

            async def get_process(self, process_id):
                return {
                    "id": "p1", "project_id": "project", "key_id": "k1",
                    "name": "process_001", "status": "ACTIVE",
                }

            async def search(self, query="", **kwargs):
                return [{"title": f"Process summary [{query}]: SUCCEEDED"}]

            async def close_process(self, process_id, status):
                return {
                    "id": "p1", "project_id": "project", "key_id": "k1",
                    "name": "process_001", "status": status,
                }

        resumed = await ProcessService(ActiveMemory("ACTIVE")).ensure_active(
            "project", "cycle-aaaaaaaaaaaaaaaaaaaa"
        )
        assert resumed["status"] == "SUCCEEDED"
        assert resumed["cycle_complete"] is True

    asyncio.run(scenario())


def test_cycle_key_stays_stable_when_newer_events_join_retry_batch():
    first = {"claimed_events": [{"id": "old"}]}
    retried = {"claimed_events": [{"id": "old"}, {"id": "new"}]}
    assert cycle_key(first) == cycle_key(retried)


def test_paginated_memories_become_graph_expansion_seeds():
    async def scenario():
        class Memory:
            def __init__(self):
                self.neighbor_ids = []

            async def search(self, **kwargs):
                assert kwargs["offset"] == 50
                return [{"id": "old-memory", "category_id": "strategy-category", "content": "old"}]

            async def get(self, memory_id):
                return {"id": memory_id, "category_id": "strategy-category"}

            async def neighbors(self, memory_id, **kwargs):
                self.neighbor_ids.append(memory_id)
                return {"Nodes": [], "Edges": []}

            async def list_processes(self, project_id):
                return [{"id": "p1"}]

        memory = Memory()
        result = await make_expand_graph(memory, max_depth=2)({
            "project_id": "project",
            "active_process_id": "p1",
            "process_context": {
                "process": {"key_id": "key"},
                "category_ids": {"STRATEGY": "strategy-category"},
                "category_pagination": {
                    "STRATEGY": {"truncated": True, "next_offset": 50},
                },
                "categories": {"STRATEGY": [{"id": "current-memory"}]},
            },
            "supervision_decision": {
                "action": "NEED_MORE_MEMORY",
                "memory_ids": [],
                "relation_types": [],
                "related_process_ids": [],
            },
        })
        assert memory.neighbor_ids == ["current-memory", "old-memory"]
        assert result["expanded_memory_context"]["category_page_exhausted"]["STRATEGY"] is True

    asyncio.run(scenario())


def test_worker_processes_root_when_opencode_context_is_temporarily_unavailable():
    async def scenario():
        calls = []

        class Inbox:
            async def claimable_roots(self):
                return ["root"]

        class OpenCode:
            async def get_context(self, root_session_id):
                raise RuntimeError("offline")

        class Runtime:
            async def run_cycle(self, root_session_id, **state):
                calls.append((root_session_id, state))
                worker.stop()
                return {"final_status": "FINALIZED"}

        worker = SupervisorWorker(Runtime(), Inbox(), OpenCode(), poll_seconds=0.001)
        await asyncio.wait_for(worker.run_forever(), timeout=0.1)
        assert calls == [("root", {"original_task": "root"})]

    asyncio.run(scenario())


def test_runtime_prefers_successful_graph_when_heartbeat_races_with_ack():
    async def scenario():
        gate = asyncio.Event()

        class Inbox:
            lease_seconds = 0.03

            async def claim_pending(self, root_session_id, limit, *, run_id):
                return [SimpleNamespace(
                    id="e1", lease_id="l1", root_session_id="root", session_id="root",
                    type="TEXT_FINAL", source_event=None, payload={},
                    received_at=datetime.now(timezone.utc), source_run_id=None,
                    sequence=None, ingress_id=None,
                )]

            async def renew_lease(self, event_id, lease_id):
                gate.set()
                await asyncio.sleep(0)
                return False

            async def fail(self, event_id, error, *, lease_id):
                raise AssertionError("a completed graph must not be failed")

        class Graph:
            async def ainvoke(self, state):
                await gate.wait()
                return {"final_status": "FINALIZED"}

        result = await SupervisorRuntime(
            Graph(), Inbox(), lease_heartbeat_seconds=0.001
        ).run_cycle("root")
        assert result["final_status"] == "FINALIZED"

    asyncio.run(scenario())


def test_mcp_tool_failure_requeues_claimed_events_without_ack(tmp_path):
    async def scenario():
        connection = await open_connection(tmp_path / "mcp-failure.db")
        repository = InboxRepository(connection)
        inbox = EventInbox(repository, lease_seconds=1)
        await inbox.initialize()
        await inbox.persist(SupervisorEventInput(
            id="mcp-event",
            payload={"type": "TOOL_CALL_FINAL"},
            type="TOOL_CALL_FINAL",
            source_event=None,
            session_id="root",
            root_session_id="root",
        ))

        class Graph:
            async def ainvoke(self, state):
                raise RuntimeError(
                    "Memory MCP process_get_active failed (-32602): unknown tool"
                )

        try:
            with pytest.raises(RuntimeError, match="unknown tool"):
                await SupervisorRuntime(Graph(), inbox).run_cycle("root")
            event = await repository.get_event("mcp-event")
            assert event.status is EventStatus.PENDING
            assert event.error is not None
            assert "process_get_active" in event.error
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_default_heartbeat_renews_short_valid_leases(tmp_path):
    async def scenario():
        connection = await open_connection(tmp_path / "heartbeat.db")
        repository = InboxRepository(connection)
        inbox = EventInbox(repository, lease_seconds=0.03)
        await inbox.initialize()
        await inbox.persist(SupervisorEventInput(
            id="e1", payload={"type": "TEXT_FINAL"}, type="TEXT_FINAL",
            source_event=None, session_id="root", root_session_id="root",
        ))

        class Graph:
            async def ainvoke(self, state):
                await asyncio.sleep(0.05)
                claims = [(event["id"], event["lease_id"]) for event in state["claimed_events"]]
                assert await inbox.ack_batch(claims) is True
                return {"final_status": "FINALIZED"}

        try:
            result = await SupervisorRuntime(Graph(), inbox).run_cycle("root")
            assert result["final_status"] == "FINALIZED"
            assert (await repository.get_event("e1")).status is EventStatus.PROCESSED
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_runtime_persists_and_reuses_original_session_objective(tmp_path):
    async def scenario():
        connection = await open_connection(tmp_path / "objective.db")
        repository = InboxRepository(connection)
        inbox = EventInbox(repository, lease_seconds=1)
        await inbox.initialize()
        captured = []

        class Graph:
            async def ainvoke(self, state):
                captured.append(state["original_task"])
                claims = [(event["id"], event["lease_id"]) for event in state["claimed_events"]]
                await inbox.ack_batch(claims)
                return state

        runtime = SupervisorRuntime(Graph(), inbox)
        try:
            await inbox.persist(SupervisorEventInput(
                id="task", payload={
                    "type": "USER_MESSAGE_FINAL",
                    "payload": {"properties": {"part": {"text": "Original objective"}}},
                }, type="USER_MESSAGE_FINAL", source_event=None,
                session_id="root", root_session_id="root",
            ))
            await runtime.run_cycle("root", original_task="later context")
            await inbox.persist(SupervisorEventInput(
                id="tool", payload={"type": "TOOL_RESULT_FINAL"}, type="TOOL_RESULT_FINAL",
                source_event=None, session_id="root", root_session_id="root",
            ))
            await runtime.run_cycle("root", original_task="wrong restart value")
            assert captured == ["Original objective", "Original objective"]
            assert await inbox.get_objective("root") == "Original objective"
        finally:
            await connection.close()

    asyncio.run(scenario())
