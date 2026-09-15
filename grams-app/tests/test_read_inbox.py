import asyncio
from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.agent.nodes.read_inbox import make_read_inbox_node
from supervisor.inbox.model import EventStatus, SupervisorEvent


def test_read_inbox_claims_serializable_events_with_leases():
    async def scenario():
        received_at = datetime.now(timezone.utc)

        class FakeInbox:
            async def claim_pending(self, root_session_id, limit, *, run_id):
                assert root_session_id == "session-1"
                assert limit == 4
                assert run_id == "run-1"
                return [SupervisorEvent(
                    id="event-1",
                    session_id="session-1",
                    root_session_id="session-1",
                    type="TEXT_FINAL",
                    source_event="message.updated",
                    payload={"text": "done"},
                    status=EventStatus.PROCESSING,
                    received_at=received_at,
                    lease_id="lease-1",
                )]

        node = make_read_inbox_node(FakeInbox(), batch_size=4, run_id="run-1")
        result = await node({"root_session_id": "session-1"})

        assert result == {
            "claimed_events": [{
                "id": "event-1",
                "lease_id": "lease-1",
                "root_session_id": "session-1",
                "session_id": "session-1",
                "type": "TEXT_FINAL",
                "source_event": "message.updated",
                "payload": {"text": "done"},
                "received_at": received_at.isoformat(),
                "source_run_id": None,
                "sequence": None,
                "ingress_id": None,
            }]
        }

    asyncio.run(scenario())


def test_read_inbox_returns_empty_batch_without_claims():
    async def scenario():
        class FakeInbox:
            async def claim_pending(self, root_session_id, limit, *, run_id):
                return []

        node = make_read_inbox_node(FakeInbox())
        assert await node({"root_session_id": "session-1"}) == {"claimed_events": []}

    asyncio.run(scenario())
