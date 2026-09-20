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
                "memories": [
                    {
                        "category": "STRATEGY",
                        "title": "Plan A",
                        "content": "Try A",
                        "candidate_ref": "new_1",
                    },
                    {
                        "category": "STRATEGY",
                        "title": "Plan A",
                        "content": "Try A",
                        "candidate_ref": "new_2",
                    },
                ],
                "relations": [],
            },
        })
        assert memory.memories[0]["description"] == "cycle-fixed:new_1,new_2"

        reloaded = await load_context({
            "project_id": "project-1",
            "active_process_id": "process-1",
        })
        with pytest.raises(RuntimeError, match="conflicts with the durable cycle proposal"):
            await apply_update({
                **reloaded,
                "claimed_events": claim,
                "proposed_memory_update": {
                    "memories": [
                        {
                            "category": "STRATEGY",
                            "title": "Plan A",
                            "content": "Try A",
                            "candidate_ref": "new_1",
                        },
                        {
                            "category": "STRATEGY",
                            "title": "Reworded plan",
                            "content": "Try A more carefully",
                            "candidate_ref": "new_2",
                        },
                    ],
                    "relations": [],
                },
            })

        assert len(memory.memories) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("has_existing_match", [False, True])
def test_apply_memory_update_omits_deduplicated_self_edges(has_existing_match):
    async def scenario():
        class Memory:
            def __init__(self):
                self.created = []
                self.links = []

            async def create(self, value):
                self.created.append(value)
                return {"id": f"m-{len(self.created)}", "category_id": value["category_id"]}

            async def link(self, *args, **kwargs):
                self.links.append((args, kwargs))
                return {"id": "edge-1"}

        memory = Memory()
        categories = {"STRATEGY": [], "EVIDENCE": []}
        if has_existing_match:
            categories["STRATEGY"].append({
                "id": "m-existing",
                "category_id": "cat-s",
                "title": "Plan",
                "content": "Try A",
            })

        result = await make_apply_memory_update(memory)({
            "process_context": {
                "category_ids": {"STRATEGY": "cat-s", "EVIDENCE": "cat-e"},
                "categories": categories,
                "relations": [],
            },
            "proposed_memory_update": {
                "memories": [
                    {"category": "STRATEGY", "title": "Plan", "content": "Try A", "candidate_ref": "new_1"},
                    {"category": "STRATEGY", "title": "Plan", "content": "Try A", "candidate_ref": "new_2"},
                ],
                "relations": [{
                    "source_id": "new_1",
                    "relation_type": "SUPPORTS",
                    "target_id": "new_2",
                }],
            },
        })

        resolved_id = "m-existing" if has_existing_match else "m-1"
        assert result["memory_update_result"]["omitted_relations"] == [{
            "source_id": "new_1",
            "relation_type": "SUPPORTS",
            "target_id": "new_2",
            "resolved_id": resolved_id,
            "reason": "deduplication_self_relation",
        }]
        assert result["memory_update_result"]["resolved_refs"] == {
            "new_1": resolved_id,
            "new_2": resolved_id,
        }
        assert len(memory.created) == (0 if has_existing_match else 1)
        assert memory.links == []

    asyncio.run(scenario())


def test_apply_memory_update_keeps_valid_relations_when_one_collapses():
    async def scenario():
        class Memory:
            def __init__(self):
                self.created = []
                self.links = []

            async def create(self, value):
                self.created.append(value)
                return {"id": f"m-{len(self.created)}", "category_id": value["category_id"]}

            async def link(self, source_id, target_id, relation, **kwargs):
                self.links.append((source_id, target_id, relation, kwargs))
                return {"id": "edge-1"}

        memory = Memory()
        result = await make_apply_memory_update(memory)({
            "process_context": {
                "category_ids": {"STRATEGY": "cat-s", "EVIDENCE": "cat-e"},
                "categories": {"STRATEGY": [], "EVIDENCE": []},
                "relations": [],
            },
            "proposed_memory_update": {
                "memories": [
                    {"category": "STRATEGY", "title": "Plan", "content": "Try A", "candidate_ref": "new_1"},
                    {"category": "STRATEGY", "title": "Plan", "content": "Try A", "candidate_ref": "new_2"},
                    {"category": "EVIDENCE", "title": "Result", "content": "A worked", "candidate_ref": "new_3"},
                ],
                "relations": [
                    {"source_id": "new_1", "relation_type": "SUPPORTS", "target_id": "new_2"},
                    {"source_id": "new_1", "relation_type": "PRODUCED", "target_id": "new_3"},
                ],
            },
        })

        update = result["memory_update_result"]
        assert len(update["omitted_relations"]) == 1
        assert len(update["created_relations"]) == 1
        assert memory.links == [("m-1", "m-2", "PRODUCED", {"source": "supervisor"})]

    asyncio.run(scenario())


def test_apply_memory_update_reconciles_tagged_memories_outside_loaded_page():
    async def scenario():
        class Memory:
            def __init__(self):
                self.created = []
                self.search_calls = []

            async def search(self, **filters):
                self.search_calls.append(filters)
                if filters.get("query") == "cycle-fixed" and filters.get("category_id") == "cat-s":
                    return [{
                        "id": "m-tagged",
                        "category_id": "cat-s",
                        "title": "Plan",
                        "content": "Try A",
                        "description": "cycle-fixed:new_1",
                    }]
                return []

            async def create(self, value):
                self.created.append(value)
                return {"id": "unexpected", "category_id": value["category_id"]}

        memory = Memory()
        result = await make_apply_memory_update(memory)({
            "project_id": "project-1",
            "claimed_events": [{"id": "event-1", "cycle_id": "cycle-fixed"}],
            "process_context": {
                "process": {"project_id": "project-1"},
                "key": {"id": "key-1"},
                "category_ids": {"STRATEGY": "cat-s", "EVIDENCE": "cat-e"},
                "categories": {"STRATEGY": [], "EVIDENCE": []},
                "category_pagination": {
                    "STRATEGY": {"truncated": True},
                    "EVIDENCE": {"truncated": False},
                },
                "relations": [],
            },
            "proposed_memory_update": {
                "memories": [
                    {"category": "STRATEGY", "title": "Plan", "content": "Try A", "candidate_ref": "new_1"},
                    {"category": "STRATEGY", "title": "Plan", "content": "Try A", "candidate_ref": "new_2"},
                ],
                "relations": [],
            },
        })

        assert result["memory_update_result"]["resolved_refs"] == {
            "new_1": "m-tagged",
            "new_2": "m-tagged",
        }
        assert memory.created == []
        assert len(memory.search_calls) == 2

    asyncio.run(scenario())


def test_apply_memory_update_rejects_malformed_literal_endpoint_before_writes():
    async def scenario():
        class Memory:
            def __init__(self):
                self.created = []
                self.get_calls = []

            async def get(self, memory_id):
                self.get_calls.append(memory_id)
                return {"id": "different-id", "category_id": "cat-s"}

            async def create(self, value):
                self.created.append(value)
                return {"id": "m-new", "category_id": value["category_id"]}

        memory = Memory()
        with pytest.raises(ValueError, match="outside the current process scope"):
            await make_apply_memory_update(memory)({
                "process_context": {
                    "category_ids": {"STRATEGY": "cat-s", "EVIDENCE": "cat-e"},
                    "categories": {"STRATEGY": [], "EVIDENCE": []},
                    "relations": [],
                },
                "proposed_memory_update": {
                    "memories": [{
                        "category": "STRATEGY",
                        "title": "Plan",
                        "content": "Try A",
                        "candidate_ref": "new_1",
                    }],
                    "relations": [{
                        "source_id": "new_1",
                        "relation_type": "SUPPORTS",
                        "target_id": "foreign",
                    }],
                },
            })

        assert memory.get_calls == ["foreign"]
        assert memory.created == []

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
