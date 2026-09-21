import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.interventions import (
    InterventionStatus,
    PendingInterventionRepository,
)
from supervisor.platform.sqlite.db import open_connection
from supervisor.agent.nodes.record_intervention import make_record_intervention
from supervisor.agent.nodes.send_intervention import make_send_intervention
from supervisor.agent.prompts import format_intervention_for_agent


def test_pending_intervention_is_session_scoped_idempotent_and_one_shot(tmp_path):
    async def scenario():
        connection = await open_connection(tmp_path / "supervisor.db")
        repository = PendingInterventionRepository(connection, claim_lease_seconds=60)
        await repository.initialize()

        first = await repository.enqueue("session-1", "Do the validation.", "cycle-1")
        retry = await repository.enqueue("session-1", "Different retry text.", "cycle-1")
        other = await repository.enqueue("session-2", "Other session.", "cycle-1")
        assert first["id"] == retry["id"]
        assert retry["message"] == "Do the validation."
        assert other["id"] != first["id"]

        claimed = await repository.claim("session-1")
        assert claimed is not None
        assert claimed["session_id"] == "session-1"
        assert claimed["status"] == InterventionStatus.CLAIMED
        assert await repository.claim("session-1") is None
        assert await repository.claim("session-2") is not None

        assert await repository.consume(
            str(claimed["id"]), "session-1", str(claimed["claim_token"])
        )
        assert await repository.claim("session-1") is None
        assert await repository.consume(
            str(claimed["id"]), "session-1", str(claimed["claim_token"])
        )
        assert not await repository.consume(
            str(claimed["id"]), "session-1", "wrong-token"
        )
        await connection.close()

    asyncio.run(scenario())


def test_expired_claim_is_not_reinjected(tmp_path):
    async def scenario():
        connection = await open_connection(tmp_path / "supervisor.db")
        repository = PendingInterventionRepository(connection, claim_lease_seconds=0.001)
        await repository.initialize()
        await repository.enqueue("session-1", "Do not duplicate.", "cycle-1")
        claimed = await repository.claim("session-1")
        assert claimed is not None
        await asyncio.sleep(0.01)
        assert await repository.claim("session-1") is None
        row = await repository.get(str(claimed["id"]), "session-1")
        assert row["status"] == InterventionStatus.DELIVERY_UNKNOWN
        await connection.close()

    asyncio.run(scenario())


def test_concurrent_same_key_enqueue_returns_one_record(tmp_path):
    async def scenario():
        path = tmp_path / "supervisor.db"
        connection = await open_connection(path)
        first = PendingInterventionRepository(connection)
        second = PendingInterventionRepository(connection)
        await first.initialize()
        results = await asyncio.gather(
            first.enqueue("session-1", "First.", "cycle-1"),
            second.enqueue("session-1", "Second.", "cycle-1"),
        )
        assert results[0]["id"] == results[1]["id"]
        assert results[0]["message"] in {"First.", "Second."}
        await connection.close()

    asyncio.run(scenario())


def test_pending_intervention_table_is_durable(tmp_path):
    async def scenario():
        path = tmp_path / "supervisor.db"
        connection = await open_connection(path)
        repository = PendingInterventionRepository(connection)
        await repository.initialize()
        await repository.enqueue("session-1", "Persist me.", "cycle-1")
        await connection.close()

        with sqlite3.connect(path) as database:
            row = database.execute(
                "SELECT session_id, message, status FROM supervisor_interventions"
            ).fetchone()
        assert row == ("session-1", "Persist me.", "PENDING")

    asyncio.run(scenario())


def test_normal_intervention_queues_without_calling_prompt_async():
    async def scenario():
        class Pending:
            def __init__(self):
                self.calls = []

            async def enqueue(self, session_id, message, delivery_key, **kwargs):
                self.calls.append((session_id, message, delivery_key))
                return {
                    "id": "intervention-1",
                    "session_id": session_id,
                    "message": message,
                    "delivery_key": delivery_key,
                    "status": InterventionStatus.PENDING,
                }

        class OpenCode:
            async def send_message(self, session_id, message):
                raise AssertionError("normal intervention delivery must not use prompt_async")

        class Memory:
            async def search(self, **kwargs):
                return []

            async def create(self, value):
                return {"id": "audit-1", **value}

            async def update(self, memory_id, value):
                return {"id": memory_id, **value}

        pending = Pending()
        memory = Memory()
        formatted_message = format_intervention_for_agent("Run the focused validation.")
        state = {
            "root_session_id": "session-1",
            "claimed_events": [{"id": "event-1", "cycle_id": "cycle-1"}],
            "intervention_message": "Run the focused validation.",
            "process_context": {"key": {"evidence_category_id": "evidence"}},
        }
        result = await make_send_intervention(
            OpenCode(),
            memory,
            pending_interventions=pending,
        )(state)
        result = await make_record_intervention(memory)({**state, **result})
        assert pending.calls == [("session-1", formatted_message, "cycle-1")]
        assert result["intervention_result"]["delivered"] is False
        assert result["intervention_result"]["delivery_status"] == "PENDING"

    asyncio.run(scenario())


def test_explicit_prompt_async_fallback_has_one_owner():
    async def scenario():
        connection = await open_connection(tmp_path / "fallback.db")
        repository = PendingInterventionRepository(connection)
        await repository.initialize()

        class OpenCode:
            def __init__(self):
                self.calls = []

            async def send_message(self, session_id, message):
                self.calls.append((session_id, message))
                return {"accepted": True}

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

        opencode = OpenCode()
        memory = Memory()
        formatted_message = format_intervention_for_agent("Use the fallback.")
        node = make_send_intervention(
            opencode,
            memory,
            pending_interventions=repository,
            fallback_mode="prompt_async",
        )
        state = {
            "root_session_id": "session-1",
            "claimed_events": [{"id": "event-1", "cycle_id": "cycle-1"}],
            "intervention_message": "Use the fallback.",
            "process_context": {"key": {"evidence_category_id": "evidence"}},
        }
        first = await node(state)
        second = await node(state)
        assert first["intervention_result"]["delivery_status"] == "FALLBACK_DELIVERED"
        assert second["intervention_result"]["delivery_status"] == "FALLBACK_DELIVERED"
        assert opencode.calls == [("session-1", formatted_message)]
        await connection.close()

    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)
        asyncio.run(scenario())
