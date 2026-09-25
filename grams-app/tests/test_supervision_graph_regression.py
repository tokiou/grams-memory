"""Exercise real graph routing around ambiguous JEV decisions."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.agent.graph import build_graph


@pytest.mark.parametrize("authorized", [False, True])
def test_graph_routes_post_expansion_intervention_only_with_explicit_choice(authorized):
    async def scenario():
        class Inbox:
            def __init__(self):
                self.acks = []

            async def claim_pending(self, root_session_id, limit, *, run_id):
                return [SimpleNamespace(
                    id="event-1", lease_id="lease-1", root_session_id=root_session_id,
                    session_id=root_session_id, type="TEXT_FINAL", source_event=None,
                    payload={"type": "TEXT_FINAL", "payload": {"properties": {"part": {"text": "working"}}}},
                    received_at=datetime.now(timezone.utc), source_run_id=None,
                    sequence=1, ingress_id="ingress-1", cycle_id="cycle-1",
                )]

            async def ack_batch(self, claims):
                self.acks.extend(claims)
                return True

        class Memory:
            def __init__(self):
                self.audit = None
                self.audit_creates = 0

            async def ensure_session_project(self, root):
                return "project-1"

            async def get_active_process(self, project):
                return {"id": "p1", "project_id": project, "key_id": "k1", "name": "process_001", "status": "ACTIVE"}

            async def get_process(self, process):
                return await self.get_active_process("project-1")

            async def list_processes(self, project):
                return [await self.get_active_process(project)]

            async def get_manifest(self):
                return {"projects": [{"id": "project-1", "keys": [{"id": "k1", "categories": [
                    {"id": "s1", "name": "STRATEGY"}, {"id": "e1", "name": "EVIDENCE"},
                    {"id": "x1", "name": "SUMMARY"},
                ]}]}]}

            async def search(self, query="", **filters):
                if query:
                    return [self.audit] if self.audit else []
                if filters.get("category_id") == "e1":
                    return [{"id": "m1", "category_id": "e1", "title": "Repeated failure", "content": "Three failed attempts"}]
                return []

            async def get(self, memory_id):
                return {"id": memory_id, "category_id": "e1", "content": "Three failed attempts"}

            async def neighbors(self, memory_id, **filters):
                return {"nodes": [{"id": "m2", "category_id": "e1", "content": "Additional evidence"}], "edges": []}

            async def create(self, payload):
                self.audit_creates += 1
                self.audit = {"id": "audit-1", **payload}
                return self.audit

            async def update(self, memory_id, payload):
                self.audit.update(payload)
                return self.audit

        class Jev:
            def __init__(self):
                self.action_calls = 0

            async def system_one(self, *, state, questions):
                if "continuity" in questions:
                    return {"answers": {"continuity": choice("SAME_PROCESS", {"SAME_PROCESS": .9, "NEW_PROCESS": .1})}}
                if "progress_stall_probability" in questions:
                    return {"answers": {name: {"type": "noul", "noul": .25 if name == "context_sufficient_probability" else .8}
                                        for name in questions}}
                if "action" in questions:
                    self.action_calls += 1
                    proposed = "INTERVENE" if authorized else "NEED_MORE_MEMORY"
                    answer = choice(proposed, {
                        "CONTINUE": .55 if authorized else .02,
                        "NEED_MORE_MEMORY": .05,
                        "INTERVENE": .3 if authorized else .9,
                        "CLOSE_PROCESS": .1 if authorized else .03,
                    })
                    if authorized:
                        answer["confidence"] = .01
                    return {"answers": {"action": answer}}
                return {"answers": {
                    "evidence_memory_1": choice("m1"),
                    "evidence_memory_2": choice("m1"),
                    "evidence_memory_3": choice("NONE"),
                    "intervention_reason_1": choice("REPEATED_FAILURE"),
                    "intervention_reason_2": choice("REPEATED_FAILURE"),
                }}

        class Generator:
            def __init__(self):
                self.interventions = 0

            async def generate_json(self, **kwargs):
                assert kwargs["operation"] == "EXTRACT_MEMORY_CANDIDATES"
                return {"candidates": []}

            async def generate_text(self, **kwargs):
                self.interventions += 1
                assert kwargs["payload"]["evidence_memory_ids"] == ["m1"]
                assert kwargs["payload"]["reason_codes"] == ["REPEATED_FAILURE"]
                return "Check the repeated failure."

        class OpenCode:
            def __init__(self):
                self.sent = []

            async def send_message(self, session_id, message):
                self.sent.append((session_id, message))
                return {"accepted": True}

        def choice(value, probabilities=None):
            return {"type": "choice", "choice": value, "confidence": .9,
                    "probabilities": probabilities or {value: 1.0}}

        inbox, memory, jev, generator, opencode = Inbox(), Memory(), Jev(), Generator(), OpenCode()
        graph = build_graph(inbox=inbox, memory=memory, jev=jev, openrouter=generator,
                            opencode=opencode, intervention_fallback="prompt_async")
        result = await graph.ainvoke({"root_session_id": "session-1", "original_task": "solve"})
        assert result["final_status"] == "FINALIZED"
        assert inbox.acks == [("event-1", "lease-1")]
        assert jev.action_calls == (1 if authorized else 2)
        assert generator.interventions == int(authorized)
        assert len(opencode.sent) == int(authorized)
        assert (memory.audit is not None) == authorized
        assert memory.audit_creates == int(authorized)
        if authorized:
            # Replay of the same durable cycle reuses the audit/delivery key.
            replay = await graph.ainvoke({"root_session_id": "session-1", "original_task": "solve"})
            assert replay["final_status"] == "FINALIZED"
            assert len(opencode.sent) == 1
            assert memory.audit_creates == 1

    asyncio.run(scenario())
