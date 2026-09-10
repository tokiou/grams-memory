import asyncio
from pathlib import Path
import sys
import tempfile

import httpx

sys.path.insert(0, str(Path(__file__).parents[1] / "supervisor"))

from supervisor.agent.graph import build_graph
from supervisor.agent.nodes.intervene import make_intervene_node
from supervisor.agent.nodes.memory_operation import make_memory_operation_node
from supervisor.agent.nodes.read_inbox import make_read_inbox_node
from supervisor.agent.nodes.review import ReviewService, make_review_node
from supervisor.config import Config
from supervisor.inbox import EventInbox, InboxRepository, SupervisorEventInput
from supervisor.platform.sqlite.db import close_checkpointer, open_checkpointer, open_connection
from supervisor.runtime import SupervisorRuntime


class OpenCodeFake:
    async def get_context(self, session_id):
        return {"session_id": session_id, "messages": []}

    async def send_message(self, session_id, message):
        return None

    async def abort_session(self, session_id):
        return None

    async def task_control(self, session_id, action, **arguments):
        return None


def make_test_review_node(inbox, opencode, model, *, memory=None, **kwargs):
    service = ReviewService(memory, **{key: value for key, value in kwargs.items()
                                      if key in {"stagnation_enabled", "stagnation_threshold_seconds"}})
    return make_review_node(inbox, opencode, service, model)


def run(coroutine):
    return asyncio.run(coroutine)


async def make_inbox(path: Path):
    connection = await open_connection(path)
    repository = InboxRepository(connection, max_attempts=3)
    inbox = EventInbox(repository, batch_size=10, lease_seconds=0.1)
    await inbox.initialize()
    return connection, inbox


def event(session_id: str, event_id: str) -> SupervisorEventInput:
    return SupervisorEventInput.from_payload({
        "id": event_id,
        "type": "MESSAGE_FINAL",
        "session_id": session_id,
        "root_session_id": session_id,
        "payload": {"id": event_id},
    })


def state_events(items):
    return [{
        "id": item.id,
        "session_id": item.session_id,
        "root_session_id": item.root_session_id,
        "type": item.type,
        "source_event": item.source_event,
        "payload": item.payload,
        "received_at": item.received_at.isoformat(),
        "lease_id": item.lease_id,
    } for item in items]


def test_restart_recovers_processing_events():
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supervisor.db"
            connection, inbox = await make_inbox(path)
            await inbox.persist(event("root", "E1"))
            claimed = await inbox.claim_batch("root", run_id="run-1")
            assert claimed[0].status.value == "PROCESSING"
            await asyncio.sleep(0.2)
            await connection.close()

            connection, inbox = await make_inbox(path)
            pending = await inbox.peek_pending("root")
            assert [item.id for item in pending] == ["E1"]
            await connection.close()

    run(scenario())


def test_events_arriving_during_memory_io_stay_out_of_active_state():
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supervisor.db"
            connection, inbox = await make_inbox(path)
            started = asyncio.Event()
            release = asyncio.Event()

            class BlockingMemory:
                async def ensure_session_hierarchy(self, root_session_id):
                    return "progress"

                async def get_manifest(self):
                    return {"projects": [{"id": "project", "name": "root", "keys": [{"categories": [{"id": "progress"}]}]}]}

                async def search(self, query, **filters):
                    started.set()
                    await release.wait()
                    return []

                async def create(self, memory):
                    return memory

            memory = BlockingMemory()
            checkpointer, checkpointer_context = await open_checkpointer(path)
            graph = build_graph(inbox, memory, OpenCodeFake(), checkpointer, config=Config(path, inbox_batch_size=10))
            await inbox.persist(event("root", "E1"))
            claimed = await inbox.claim_pending("root", run_id="run-1")
            claimed_state = state_events(claimed)
            execution = asyncio.create_task(graph.ainvoke({"root_session_id": "root", "claimed_events": claimed_state}, config={"configurable": {"thread_id": "root"}}))
            await asyncio.wait_for(started.wait(), 2)
            await inbox.persist(event("root", "E2"))
            assert [item.id for item in await inbox.peek_pending("root")] == ["E2"]
            # The checkpoint may still expose the pre-READ_INBOX snapshot while
            # the review is blocked in MCP I/O; the new event must remain pending.
            assert [item.id for item in await inbox.peek_pending("root")] == ["E2"]
            release.set()
            result = await execution
            assert [item["id"] for item in result["current_events"]] == ["E1"]
            assert [item["id"] for item in result["processing_events"]] == ["E1"]
            for item in result["processing_events"]:
                await inbox.mark_processed(item["id"], item.get("lease_id"))
            await close_checkpointer(checkpointer_context)
            await connection.close()

    run(scenario())


def test_same_thread_restores_operational_state():
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supervisor.db"
            connection, inbox = await make_inbox(path)
            class Memory:
                async def ensure_session_hierarchy(self, root_session_id):
                    return "progress"

                async def get_manifest(self):
                    return {"projects": [{"id": "project", "name": "root", "keys": [{"categories": [{"id": "progress"}]}]}]}

                async def search(self, query, **filters):
                    return []

                async def create(self, memory):
                    return memory

            memory = Memory()
            checkpointer, checkpointer_context = await open_checkpointer(path)
            graph = build_graph(inbox, memory, OpenCodeFake(), checkpointer, config=Config(path, inbox_batch_size=10))

            await inbox.persist(event("root", "E1"))
            first_claim = await inbox.claim_pending("root", run_id="run-1")
            first = await graph.ainvoke(
                {"root_session_id": "root", "claimed_events": state_events(first_claim)},
                config={"configurable": {"thread_id": "root"}},
            )
            for item in first["processing_events"]:
                await inbox.mark_processed(item["id"], item["lease_id"])

            await inbox.persist(event("root", "E2"))
            second_claim = await inbox.claim_pending("root", run_id="run-2")
            await graph.ainvoke(
                {"root_session_id": "root", "claimed_events": state_events(second_claim)},
                config={"configurable": {"thread_id": "root"}},
            )
            state = await graph.aget_state({"configurable": {"thread_id": "root"}})
            assert [item["id"] for item in state.values["current_events"]] == ["E1", "E2"]
            await close_checkpointer(checkpointer_context)
            await connection.close()

    run(scenario())


def test_runtime_has_one_active_graph_invocation():
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supervisor.db"
            connection, inbox = await make_inbox(path)
            await inbox.persist(event("root", "E1"))
            active = 0
            maximum = 0
            release = asyncio.Event()

            class FakeGraph:
                async def ainvoke(self, state, config):
                    nonlocal active, maximum
                    active += 1
                    maximum = max(maximum, active)
                    await release.wait()
                    active -= 1
                    return {"processing_events": []}

            runtime = SupervisorRuntime(inbox, FakeGraph(), poll_interval=0.01)
            first = asyncio.create_task(runtime.run_once())
            await asyncio.sleep(0)
            second = asyncio.create_task(runtime.run_once())
            await asyncio.sleep(0.01)
            assert maximum == 1
            release.set()
            await asyncio.gather(first, second)
            assert maximum == 1
            await runtime.stop()
            await connection.close()

    run(scenario())


def test_intervention_without_real_session_is_skipped():
    async def scenario():
        calls = []

        class OpenCode:
            async def send_message(self, *args, **kwargs):
                calls.append((args, kwargs))

        result = await make_intervene_node(OpenCode())({
            "root_session_id": "default",
            "pending_intervention": {"message": "do not send this"},
        })

        assert result["pending_intervention"] is None
        assert result["last_intervention"]["skipped"] is True
        assert calls == []

    run(scenario())


def test_read_inbox_preserves_original_task_from_user_message():
    async def scenario():
        result = await make_read_inbox_node(limit=20)({
            "root_session_id": "root",
            "claimed_events": [{
                "id": "E1",
                "type": "USER_MESSAGE_FINAL",
                "payload": {
                    "payload": {
                        "properties": {
                            "part": {"text": "Configure the service"},
                        },
                    },
                },
            }],
        })

        assert result["original_task"] == "Configure the service"

    run(scenario())


def test_memory_create_normalizes_confidence_labels():
    async def scenario():
        captured = {}

        class Memory:
            async def create(self, memory):
                captured.update(memory)
                return {"id": "M1"}

        result = await make_memory_operation_node(Memory(), "category")({
            "memory_operation": {
                "operation": "create",
                "arguments": {
                    "content": "validated result",
                    "project_id": "project",
                    "key_id": "key",
                    "confidence": "high",
                    "type": "observation",
                    "status": "active",
                    "graph_tier": "active",
                },
            },
        })

        assert captured["confidence"] == 0.9
        assert captured["type"] == "OBSERVATION"
        assert captured["status"] == "ACTIVE"
        assert captured["graph_tier"] == "ACTIVE"
        assert "project_id" not in captured
        assert "key_id" not in captured
        assert result["memory_operation_result"]["operation"] == "create"

    run(scenario())


def test_memory_create_with_existing_candidate_schedules_a_real_link():
    async def scenario():
        memories = {"prior": {"id": "prior"}}
        links = []

        class Memory:
            async def create(self, memory):
                created = {"id": "created", **memory}
                memories["created"] = created
                return created

            async def get(self, memory_id):
                return memories.get(memory_id)

            async def link(self, source, target, relation, **metadata):
                links.append((source, target, relation, metadata))
                return {"id": "edge-1"}

        node = make_memory_operation_node(Memory(), "category")
        created = await node({
            "memory_operation": {"operation": "create", "arguments": {"content": "new observation"}},
            "retrieved_memories": [{"id": "prior"}],
        })

        assert created["memory_operation"]["operation"] == "link"
        assert created["memory_operation"]["arguments"]["source"] == "created"
        assert created["memory_operation"]["arguments"]["target"] == "prior"

        linked = await node({"memory_operation": created["memory_operation"]})
        assert linked["memory_operation_result"]["operation"] == "link"
        assert links[0][:3] == ("created", "prior", "SUPPORTS")

    run(scenario())


def test_review_model_timeout_uses_deterministic_fallback():
    async def scenario():
        class Inbox:
            async def pending_count(self, root_session_id):
                return 0

        class Model:
            async def decide(self, context):
                raise httpx.ReadTimeout("model timed out")

        result = await make_test_review_node(Inbox(), OpenCodeFake(), Model())({
            "root_session_id": "default",
            "current_events": [{"id": "E1", "type": "TOOL_RESULT_FINAL"}],
        })

        assert result["next_action"] == "MEMORY_OPERATION"
        assert result["memory_operation"]["operation"] == "search"

    run(scenario())


def test_review_does_not_call_external_services_before_reading_claimed_events():
    async def scenario():
        calls = []

        class OpenCode:
            async def get_context(self, session_id):
                calls.append("opencode")
                return {"session_id": session_id, "messages": []}

        class Model:
            async def decide(self, context):
                calls.append("model")
                return {"action": "DONE"}

        class Inbox:
            async def pending_count(self, root_session_id):
                return 0

        result = await make_test_review_node(Inbox(), OpenCode(), Model())({
            "root_session_id": "session",
            "claimed_events": [{"id": "E1"}],
        })

        assert result["next_action"] == "READ_INBOX"
        assert calls == []

    run(scenario())


def test_review_removes_embedded_link_from_memory_arguments():
    async def scenario():
        class Model:
            async def decide(self, context):
                return {
                    "action": "MEMORY_OPERATION",
                    "reason": "persist observation",
                    "operation": "create",
                    "arguments": {
                        "content": "new observation",
                        "link": {"target_id": "prior", "relation": "SUPPORTS"},
                    },
                }

        class Inbox:
            async def pending_count(self, root_session_id):
                return 0

        result = await make_test_review_node(Inbox(), OpenCodeFake(), Model())({
            "root_session_id": "default",
            "current_events": [{"id": "E1", "type": "TOOL_RESULT_FINAL"}],
        })

        assert "link" not in result["memory_operation"]["arguments"]
        assert result["memory_link_request"]["target_id"] == "prior"

    run(scenario())


def test_review_propagates_full_memory_hierarchy_from_category():
    async def scenario():
        class Model:
            async def decide(self, context):
                return {
                    "action": "MEMORY_OPERATION",
                    "reason": "persist discovery",
                    "operation": "create",
                    "arguments": {
                        "category_name": "discoveries",
                        "content": "The build requires compiler X.",
                    },
                }

        class Inbox:
            async def pending_count(self, root_session_id):
                return 0

        result = await make_test_review_node(Inbox(), OpenCodeFake(), Model())({
            "root_session_id": "default",
            "memory_manifest": {
                "projects": [{
                    "id": "P1",
                    "name": "default",
                    "keys": [{
                        "id": "K1",
                        "name": "execution",
                        "categories": [{"id": "C1", "name": "discoveries"}],
                    }],
                }],
            },
            "current_events": [{"id": "E1", "type": "TOOL_RESULT_FINAL"}],
        })

        arguments = result["memory_operation"]["arguments"]
        assert arguments["project_id"] == "P1"
        assert arguments["key_id"] == "K1"
        assert arguments["category_id"] == "C1"

    run(scenario())


def test_review_reads_session_memory_before_model():
    async def scenario():
        calls = []

        class Memory:
            async def ensure_session_hierarchy(self, root_session_id):
                return "progress"

            async def get_manifest(self):
                return {"projects": [{"id": "P1", "name": "session", "keys": [{"id": "K1", "categories": [{"id": "progress"}]}]}]}

            async def search(self, query, **filters):
                calls.append(("search", query, filters))
                return [{"id": "M1", "content": "prior context"}]

        class Model:
            async def decide(self, context):
                calls.append(("model", context["retrieved_memories"]))
                return {"action": "DONE", "reason": "context reviewed"}

        result = await make_test_review_node(
            type("Inbox", (), {"pending_count": lambda self, root: asyncio.sleep(0, result=0)})(),
            OpenCodeFake(), Model(), memory=Memory(),
        )({"root_session_id": "session", "current_events": [{"id": "E1", "type": "TOOL_RESULT_FINAL"}]})

        assert calls[0][0] == "search"
        assert calls[1] == ("model", [{"id": "M1", "content": "prior context"}])
        assert result["memory_read_status"] == "COMPLETED"

    run(scenario())


def test_review_intervenes_for_configured_stagnation():
    async def scenario():
        old = "2020-01-01T00:00:00+00:00"

        class Model:
            async def decide(self, context):
                return {"action": "DONE", "reason": "nothing new"}

        result = await make_test_review_node(
            type("Inbox", (), {"pending_count": lambda self, root: asyncio.sleep(0, result=0)})(),
            OpenCodeFake(), Model(), stagnation_threshold_seconds=1,
        )({
            "root_session_id": "default",
            "current_events": [{"id": "E1", "type": "TOOL_RESULT_FINAL", "received_at": old}],
            "activity_started_at": old,
            "last_progress_at": old,
        })

        assert result["next_action"] == "INTERVENE"
        assert result["assessment"]["decision_source"] == "deterministic"

    run(scenario())


def test_runtime_runs_tick_for_active_session_without_inbox_event():
    async def scenario():
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supervisor.db"
            connection, inbox = await make_inbox(path)
            calls = []

            class Graph:
                async def ainvoke(self, state, config):
                    calls.append(state)
                    return {
                        "processing_events": state.get("claimed_events", []),
                        "session_status": "active" if len(calls) == 1 else "idle",
                        "next_action": "DONE",
                    }

            runtime = SupervisorRuntime(inbox, Graph(), poll_interval=0.01, tick_interval=60)
            await inbox.persist(event("root", "E1"))
            assert await runtime.run_once()
            runtime._active_sessions["root"]["last_tick"] -= 61
            assert await runtime.run_once()
            assert calls[1]["supervisor_tick"] is True
            assert calls[1]["claimed_events"] == []
            assert await runtime.run_once() is False
            await runtime.stop()
            await connection.close()

    run(scenario())


def test_tick_refreshes_session_memory_before_model():
    async def scenario():
        calls = []

        class Memory:
            async def ensure_session_hierarchy(self, root_session_id):
                return "progress"

            async def get_manifest(self):
                return {"projects": [{"id": "P1", "name": "session", "keys": [{"id": "K1", "categories": [{"id": "progress"}]}]}]}

            async def search(self, query, **filters):
                calls.append("search")
                return [{"id": f"M{len(calls)}"}]

        class Model:
            async def decide(self, context):
                calls.append("model")
                return {"action": "DONE", "reason": "reviewed"}

        node = make_test_review_node(
            type("Inbox", (), {"pending_count": lambda self, root: asyncio.sleep(0, result=0)})(),
            OpenCodeFake(), Model(), memory=Memory(),
        )
        state = {"root_session_id": "session", "current_events": [{"id": "E1"}], "memory_read_status": "COMPLETED"}
        await node(state)
        await node({**state, "supervisor_tick": True})

        assert calls == ["model", "search", "model"]

    run(scenario())


def test_read_inbox_counts_each_progress_event():
    async def scenario():
        result = await make_read_inbox_node(limit=20)({
            "root_session_id": "root",
            "claimed_events": [
                {"id": "E1", "type": "TOOL_RESULT_FINAL", "received_at": "2026-01-01T00:00:00+00:00", "payload": {}},
                {"id": "E2", "type": "FILE_CHANGE_FINAL", "received_at": "2026-01-01T00:00:01+00:00", "payload": {}},
            ],
        })
        assert result["progress_count"] == 2
        assert result["last_progress_kind"] == "FILE_CHANGE_FINAL"

    run(scenario())


def test_model_contract_error_is_observable_fallback():
    async def scenario():
        class Model:
            async def decide(self, context):
                raise ValueError("invalid envelope")

        result = await make_test_review_node(
            type("Inbox", (), {"pending_count": lambda self, root: asyncio.sleep(0, result=0)})(),
            OpenCodeFake(), Model(),
        )({"root_session_id": "default", "current_events": [{"id": "E1"}]})
        assert result["assessment"]["decision_source"] == "fallback"
        assert result["next_action"] == "MEMORY_OPERATION"

    run(scenario())


def test_memory_read_failure_blocks_model_and_returns_retry_intervention():
    async def scenario():
        calls = []

        class Memory:
            async def ensure_session_hierarchy(self, root_session_id):
                return "progress"

            async def get_manifest(self):
                return {"projects": [{"id": "P1", "name": "session", "keys": [{"id": "K1", "categories": [{"id": "progress"}]}]}]}

            async def search(self, query, **filters):
                raise RuntimeError("MCP unavailable")

        class Model:
            async def decide(self, context):
                calls.append("model")
                return {"action": "DONE"}

        result = await make_test_review_node(
            type("Inbox", (), {"pending_count": lambda self, root: asyncio.sleep(0, result=0)})(),
            OpenCodeFake(), Model(), memory=Memory(),
        )({"root_session_id": "session", "current_events": [{"id": "E1"}]})

        assert calls == []
        assert result["memory_read_status"] == "FAILED"
        assert result["next_action"] == "INTERVENE"
        assert result["assessment"]["decision_source"] == "deterministic"

    run(scenario())
