import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.agent.nodes.apply_memory_update import make_apply_memory_update
from supervisor.agent.nodes.build_intervention import make_build_intervention
from supervisor.agent.nodes.expand_graph import make_expand_graph
from supervisor.agent.nodes.finalize_cycle import make_finalize_cycle
from supervisor.agent.nodes.record_intervention import make_record_intervention
from supervisor.agent.nodes.send_intervention import make_send_intervention
from supervisor.agent.nodes.supervision_decision import make_supervision_decision
from supervisor.agent.graph import build_graph
from supervisor.agent.prompts import format_intervention_for_agent
from supervisor.agent.runtime import SupervisorRuntime
from supervisor.agent.services.openrouter_service import OpenRouterClient
from supervisor.agent.schemas import validate_memory_proposal, validate_summary
from supervisor.agent.state_builder import build_jev_process_state
from supervisor.agent.state_builder import compact_jev_state, estimate_json_tokens
from supervisor.agent.services.jev_service import JevClient


class FakeJev:
    def __init__(self, values):
        self.values = list(values)
        self.calls = []

    async def system_one(self, *, state, questions):
        self.calls.append((state, questions))
        return {"answers": self.values.pop(0)}


def diagnostics():
    return {
        "progress_stall_probability": {"type": "noul", "noul": 0.7},
        "strategy_supported_probability": {"type": "noul", "noul": 0.4},
        "context_sufficient_probability": {"type": "noul", "noul": 0.2},
    }


def test_openrouter_uses_json_serialization_and_json_schema_response_format():
    async def scenario():
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            content = '{"content":"summary"}' if len(requests) == 1 else "plain text"
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = OpenRouterClient(api_key="test-key", model="test-model", http=http)
        schema = {"type": "object", "properties": {"content": {"type": "string"}}}
        value = await client.generate_json(
            operation="WRITE_PROCESS_SUMMARY",
            payload={"b": 2, "a": 1},
            system_prompt="system-json",
            schema=schema,
        )
        text = await client.generate_text(
            operation="BUILD_INTERVENTION",
            payload={"message": "x"},
            system_prompt="system-text",
        )
        await http.aclose()

        assert value == {"content": "summary"}
        assert text == "plain text"
        assert requests[0]["messages"][1]["content"] == '{"a": 1, "b": 2}'
        assert "max_tokens" not in requests[0]
        assert requests[0]["reasoning"] == {"enabled": False}
        assert requests[0]["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "write_process_summary",
                "strict": True,
                "schema": schema,
            },
        }
        assert "response_format" not in requests[1]
        assert "max_tokens" not in requests[1]
        assert requests[1]["reasoning"] == {"enabled": False}

    asyncio.run(scenario())


def test_openrouter_chat_omits_default_cap_and_accepts_explicit_positive_caps():
    async def scenario():
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = OpenRouterClient(api_key="test-key", model="test-model", http=http)
        await client.chat([{"role": "user", "content": "x"}])
        await client.chat([{"role": "user", "content": "x"}], max_tokens=8192)
        await http.aclose()

        assert "max_tokens" not in requests[0]
        assert requests[1]["max_tokens"] == 8192
        assert requests[0]["reasoning"] == {"enabled": False}

    asyncio.run(scenario())


def test_openrouter_rejects_length_truncated_responses():
    async def scenario():
        def handler(request):
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "length", "message": {"content": "{"}}],
            })

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = OpenRouterClient(api_key="test-key", model="test-model", http=http)
        with pytest.raises(RuntimeError, match="truncated by max_tokens"):
            await client.generate_json(
                operation="EXTRACT_MEMORY_UPDATE",
                payload={},
                system_prompt="system",
                schema={"type": "object"},
            )
        await http.aclose()

    asyncio.run(scenario())


def test_memory_and_summary_validators_enforce_output_bounds():
    candidate = {"category": "EVIDENCE", "title": "title", "content": "content", "candidate_ref": "new_1"}
    with pytest.raises(ValueError, match="more than 6 memories"):
        validate_memory_proposal({"memories": [{**candidate, "candidate_ref": f"new_{i}"} for i in range(1, 8)], "relations": []})
    with pytest.raises(ValueError, match="titles cannot exceed"):
        validate_memory_proposal({"memories": [{**candidate, "title": "x" * 201}], "relations": []})
    with pytest.raises(ValueError, match="content cannot exceed"):
        validate_memory_proposal({"memories": [{**candidate, "content": "x" * 1201}], "relations": []})
    with pytest.raises(ValueError, match="more than 12 relations"):
        validate_memory_proposal({
            "memories": [candidate],
            "relations": [{"source_id": "new_1", "relation_type": "SUPPORTS", "target_id": f"m{i}"} for i in range(13)],
        })
    with pytest.raises(ValueError, match="summary content cannot exceed"):
        validate_summary({"content": "x" * 4001, "outcome": "FAILED"})


def test_state_builder_normalizes_nested_plugin_payload_and_excludes_budgets():
    state = build_jev_process_state({
        "original_task": "solve",
        "deadline": "secret-deadline",
        "timeout": 10,
        "process_context": {
            "process": {"ID": "p1", "Name": "process_001", "Status": "ACTIVE"},
            "categories": {"STRATEGY": [], "EVIDENCE": []},
            "relations": [],
        },
        "claimed_events": [{
            "id": "e1",
            "type": "TEXT_FINAL",
            "received_at": "2026-09-18T10:00:00+00:00",
            "payload": {
                "type": "TEXT_FINAL",
                "timestamp": "2026-09-18T09:59:59Z",
                "timeout": 999,
                "payload": {"properties": {"part": {"text": "result"}}},
            },
        }],
    })
    serialized = json.dumps(state)
    assert state["recent_execution"][0]["text"] == "result"
    assert state["operational_metrics"]["recent_event_count"] == 1
    assert "timeout" not in serialized
    assert "deadline" not in serialized


def test_state_builder_allowlists_memory_fields_and_keeps_bounded_tool_results():
    state = build_jev_process_state({
        "process_context": {
            "process": {"ID": "p1", "Name": "process_001", "Status": "ACTIVE", "timeout": 999},
            "categories": {
                "STRATEGY": [{
                    "ID": "m1", "CategoryID": "s1", "Title": "Plan", "Content": "Use tests",
                    "timeout": 999,
                }],
                "EVIDENCE": [],
            },
            "relations": [],
        },
        "claimed_events": [{
            "id": "e1",
            "type": "TOOL_RESULT_FINAL",
            "received_at": "2026-09-18T10:00:00Z",
            "payload": {
                "type": "TOOL_RESULT_FINAL",
                "payload": {
                    "input": {"tool": "bash"},
                    "output": {
                        "args": {"command": "pytest -q", "token": "secret"},
                        "output": "33 passed",
                        "metadata": {"exit": 0},
                    },
                },
            },
        }],
    })
    serialized = json.dumps(state)
    assert state["strategy"] == [{
        "id": "m1", "category_id": "s1", "title": "Plan", "content": "Use tests",
    }]
    assert state["recent_execution"][0]["result"] == "33 passed"
    assert state["recent_execution"][0]["exit_code"] == 0
    assert state["operational_metrics"]["recent_validation_count"] == 1
    assert "timeout" not in serialized
    assert "secret" not in serialized


def test_state_builder_normalizes_real_go_subgraph_fields():
    state = build_jev_process_state({
        "process_context": {"process": {}, "categories": {}, "relations": []},
        "claimed_events": [],
        "expanded_memory_context": {
            "subgraphs": {
                "m1": {
                    "Nodes": [{"ID": "m2", "CategoryID": "e1", "Content": "older evidence"}],
                    "Edges": [{"SourceID": "m1", "TargetID": "m2", "Relation": "SUPPORTS"}],
                },
            },
        },
    })
    assert state["expanded_memory"]["subgraphs"]["m1"] == {
        "nodes": [{"id": "m2", "category_id": "e1", "content": "older evidence"}],
        "edges": [{"source_id": "m1", "target_id": "m2", "relation": "SUPPORTS"}],
    }


def test_jev_state_compaction_is_deterministic_and_marks_omissions():
    state = {
        "task": {"objective": "solve"},
        "current_process": {"id": "p1"},
        "recent_execution": [{"id": str(index), "result": "x" * 100} for index in range(5)],
        "expanded_memory": {"subgraphs": {str(index): {"nodes": [{"id": str(index)}]} for index in range(5)}},
    }
    first = compact_jev_state(state, max_tokens=250)
    second = compact_jev_state(state, max_tokens=250)
    assert first == second
    assert first["context_compaction"]["truncated"] is True
    assert first["context_compaction"]["omitted_items"]
    assert json.dumps(first, separators=(",", ":"))


def test_jev_client_compacts_before_posting():
    async def scenario():
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"answers": {}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = JevClient(api_key="test-key", http=http, max_context_tokens=1200)
        await client.system_one(
            state={"recent_execution": [{"id": str(index), "result": "x" * 100} for index in range(10)]},
            questions={"decision": {"type": "noul", "instructions": "ok"}},
        )
        await http.aclose()
        body = requests[0]
        assert body["state"]["context_compaction"]["truncated"] is True
        assert estimate_json_tokens(body) <= 1200

    asyncio.run(scenario())


def test_jev_client_budgets_the_complete_compact_request_with_unicode_and_questions():
    async def scenario():
        requests = []

        def handler(request):
            requests.append(request.content)
            return httpx.Response(200, json={"answers": {}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = JevClient(api_key="test-key", http=http, max_context_tokens=900)
        state = {
            "task": {"objective": "áéíóú 日本語 🚀"},
            "recent_execution": [{"id": str(index), "result": "x" * 200} for index in range(10)],
        }
        questions = {
            "diagnostic": {"type": "noul", "instructions": "Decide whether context is sufficient."},
            "continuity": {"type": "choice", "criteria": {"SAME_PROCESS": "same", "NEW_PROCESS": "new"}},
        }
        await client.system_one(state=state, questions=questions)
        await http.aclose()

        assert len(requests) == 1
        assert len(requests[0]) <= 900
        assert b"  " not in requests[0]
        assert json.loads(requests[0])["state"]["context_compaction"]["truncated"] is True

    asyncio.run(scenario())


def test_jev_client_rejects_questions_that_leave_no_request_budget():
    async def scenario():
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"answers": {}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = JevClient(api_key="test-key", http=http, max_context_tokens=100)
        with pytest.raises(ValueError, match="request byte limit"):
            await client.system_one(
                state={"task": {"objective": "solve"}},
                questions={"decision": {"type": "noul", "instructions": "x" * 200}},
            )
        await http.aclose()
        assert calls == 0

    asyncio.run(scenario())


def test_expansion_depth_has_a_hard_three_round_limit():
    with pytest.raises(ValueError, match="cannot exceed 3"):
        make_expand_graph(object(), max_depth=4)

    async def scenario():
        node = make_expand_graph(object())
        with pytest.raises(ValueError, match="integer from zero to three"):
            await node({"memory_expansion_depth": -1})

    asyncio.run(scenario())


def test_supervision_need_more_memory_has_python_targets_and_preserves_answers():
    async def scenario():
        action = {
            "action": {
                "type": "choice",
                "choice": "NEED_MORE_MEMORY",
                "probabilities": {
                    "CONTINUE": 0.1,
                    "NEED_MORE_MEMORY": 0.7,
                    "INTERVENE": 0.1,
                    "CLOSE_PROCESS": 0.1,
                },
                "confidence": 0.88,
            },
        }
        jev = FakeJev([diagnostics(), action])
        result = await make_supervision_decision(jev)({
            "active_process_id": "p1",
            "process_context": {
                "process": {"id": "p1"},
                "categories": {
                    "STRATEGY": [{"id": "m1"}],
                    "EVIDENCE": [{"id": "m2"}],
                },
                "relations": [],
                "related_process_ids": ["p0"],
            },
            "claimed_events": [],
        })
        decision = result["supervision_decision"]
        assert decision["memory_ids"] == ["m1", "m2"]
        assert decision["related_process_ids"] == ["p0"]
        assert decision["summaries"] is True
        assert decision["action_confidence"] == 0.88
        assert set(jev.calls[1][0]["diagnostics"]) == set(diagnostics())
        assert set(jev.calls[1][1]["action"]["criteria"]) == {
            "CONTINUE", "NEED_MORE_MEMORY", "INTERVENE", "CLOSE_PROCESS",
        }
        assert "options" not in jev.calls[1][1]["action"]

    asyncio.run(scenario())


def test_apply_memory_update_deduplicates_and_resolves_local_refs():
    async def scenario():
        class Memory:
            def __init__(self):
                self.created = []
                self.links = []

            async def create(self, value):
                self.created.append(value)
                return {"ID": "m-new", "CategoryID": value["category_id"]}

            async def link(self, source_id, target_id, relation, **metadata):
                self.links.append((source_id, target_id, relation, metadata))
                return {"id": "edge-1"}

        memory = Memory()
        result = await make_apply_memory_update(memory)({
            "process_context": {
                "category_ids": {"STRATEGY": "cat-s", "EVIDENCE": "cat-e", "SUMMARY": "cat-x"},
                "categories": {
                    "STRATEGY": [{
                        "id": "m-existing",
                        "category_id": "cat-s",
                        "title": "Plan",
                        "content": "Try A",
                    }],
                    "EVIDENCE": [],
                    "SUMMARY": [],
                },
                "relations": [],
            },
            "proposed_memory_update": {
                "memories": [
                    {"category": "STRATEGY", "title": "Plan", "content": "Try A", "candidate_ref": "new_1"},
                    {"category": "EVIDENCE", "title": "Result", "content": "A failed", "candidate_ref": "new_2"},
                ],
                "relations": [{
                    "source_id": "new_1", "relation_type": "TESTED_BY", "target_id": "new_2",
                }],
            },
        })
        update = result["memory_update_result"]
        assert len(memory.created) == 1
        assert update["resolved_refs"] == {"new_1": "m-existing", "new_2": "m-new"}
        assert memory.links == [("m-existing", "m-new", "TESTED_BY", {"source": "supervisor"})]

    asyncio.run(scenario())


def test_apply_memory_update_rejects_out_of_scope_relation_before_writes():
    async def scenario():
        class Memory:
            def __init__(self):
                self.created = []

            async def create(self, value):
                self.created.append(value)

        memory = Memory()
        node = make_apply_memory_update(memory)
        with pytest.raises(ValueError, match="outside the current process scope"):
            await node({
                "process_context": {
                    "category_ids": {"STRATEGY": "cat-s", "EVIDENCE": "cat-e"},
                    "categories": {"STRATEGY": [], "EVIDENCE": []},
                    "relations": [],
                },
                "proposed_memory_update": {
                    "memories": [{
                        "category": "STRATEGY", "title": "Plan", "content": "A", "candidate_ref": "new_1",
                    }],
                    "relations": [{
                        "source_id": "new_1", "relation_type": "SUPPORTS", "target_id": "foreign",
                    }],
                },
            })
        assert memory.created == []

    asyncio.run(scenario())


def test_expand_graph_handles_all_targets_and_stops_at_depth_limit():
    async def scenario():
        class Memory:
            def __init__(self):
                self.neighbor_calls = []

            async def get(self, memory_id):
                return {"id": memory_id, "category_id": "cat-current"}

            async def neighbors(self, memory_id, **filters):
                self.neighbor_calls.append((memory_id, filters))
                return {"nodes": [{"id": memory_id}], "edges": []}

            async def list_processes(self, project_id):
                return [
                    {"id": "p1", "project_id": project_id, "key_id": "k1", "name": "process_001", "status": "ACTIVE"},
                    {"id": "p0", "project_id": project_id, "key_id": "k0", "name": "process_000", "status": "SUPERSEDED"},
                ]

            async def get_process(self, process_id):
                return {"id": process_id, "project_id": "project-1", "key_id": "k0", "name": "process_000", "status": "SUPERSEDED"}

            async def get_manifest(self):
                return {"projects": [{"id": "project-1", "keys": [{
                    "id": "k0",
                    "name": "process_000",
                    "categories": [
                        {"id": "s0", "name": "STRATEGY"},
                        {"id": "e0", "name": "EVIDENCE"},
                        {"id": "x0", "name": "SUMMARY"},
                    ],
                }]}]}

            async def search(self, query="", **filters):
                if filters["category_id"] == "x0":
                    return [{"id": "summary-0", "category_id": "x0", "content": "old summary"}]
                return []

        memory = Memory()
        node = make_expand_graph(memory, max_depth=1)
        state = {
            "project_id": "project-1",
            "active_process_id": "p1",
            "process_context": {
                "categories": {"STRATEGY": [{"id": "m1"}], "EVIDENCE": [], "SUMMARY": []},
                "summary": {"id": "summary-1"},
            },
            "supervision_decision": {
                "action": "NEED_MORE_MEMORY",
                "memory_ids": ["m1"],
                "relation_types": ["SUPPORTS"],
                "related_process_ids": ["p0"],
                "summaries": True,
            },
        }
        result = await node(state)
        assert result["expanded_memory_context"]["summaries"]["p0"]["content"] == "old summary"
        assert result["expanded_memory_context"]["summaries"]["p1"]["id"] == "summary-1"
        assert ("m1", {"depth": 1, "relations": ["SUPPORTS"]}) in memory.neighbor_calls
        exhausted = await node({**state, **result})
        assert exhausted == {"memory_expansion_exhausted": True, "memory_expansion_depth": 1}

    asyncio.run(scenario())


def test_intervention_generation_delivery_and_evidence_audit_are_separate():
    async def scenario():
        class Generator:
            async def generate_text(self, **kwargs):
                assert "guidance" not in kwargs["payload"]
                return "Run the focused validation."

        class OpenCode:
            def __init__(self):
                self.calls = []

            async def send_message(self, session_id, message):
                self.calls.append(("send", session_id, message))
                assert session_id == "session-1"
                return {"accepted": True}

        class Memory:
            async def search(self, **kwargs):
                return []

            async def create(self, value):
                assert value["category_id"] == "evidence-category"
                return {"ID": "audit-1", "CategoryID": "evidence-category", **value}

            async def update(self, memory_id, value):
                assert memory_id == "audit-1"
                return {"ID": memory_id, "CategoryID": "evidence-category", **value}

        state = {
            "root_session_id": "session-1",
            "claimed_events": [{"id": "event-1", "lease_id": "lease-1"}],
            "supervision_decision": {"action": "INTERVENE"},
            "supervision_diagnostics": diagnostics(),
            "process_context": {
                "process": {"id": "p1"},
                "categories": {},
                "key": {"evidence_category_id": "evidence-category"},
            },
        }
        state.update(await make_build_intervention(Generator())(state))
        memory = Memory()
        opencode = OpenCode()
        state.update(await make_send_intervention(opencode, memory, fallback_mode="prompt_async")(state))
        state.update(await make_record_intervention(memory)(state))
        assert state["intervention_result"]["audit_memory_id"] == "audit-1"
        assert opencode.calls == [(
            "send",
            "session-1",
            format_intervention_for_agent("Run the focused validation."),
        )]

    asyncio.run(scenario())


def test_finalize_and_runtime_use_exact_event_leases_on_success_and_failure():
    async def scenario():
        class Inbox:
            def __init__(self):
                self.acks = []
                self.failures = []
                self.events = []

            async def ack_batch(self, claims):
                self.acks.extend(claims)
                return True

            async def claim_pending(self, root_session_id, limit, *, run_id):
                return self.events

            async def fail(self, event_id, error, *, lease_id):
                self.failures.append((event_id, lease_id, error))

        inbox = Inbox()
        final = await make_finalize_cycle(inbox)({
            "claimed_events": [{"id": "e1", "lease_id": "l1"}],
        })
        assert inbox.acks == [("e1", "l1")]
        assert final["claimed_events"] == []

        class FailingGraph:
            async def ainvoke(self, state):
                raise RuntimeError("boom")

        inbox.events = [SimpleNamespace(
            id="e2",
            lease_id="l2",
            root_session_id="session-1",
            session_id="session-1",
            type="TEXT_FINAL",
            source_event="message.part.updated",
            payload={"type": "TEXT_FINAL"},
            received_at=datetime.now(timezone.utc),
            source_run_id=None,
            sequence=None,
            ingress_id="ingress-1",
        )]
        runtime = SupervisorRuntime(FailingGraph(), inbox)
        with pytest.raises(RuntimeError, match="boom"):
            await runtime.run_cycle("session-1")
        assert inbox.failures[0][:2] == ("e2", "l2")

        inbox.events = []
        empty = await runtime.run_cycle("session-1")
        assert empty["final_status"] == "NO_EVENTS"

    asyncio.run(scenario())


def test_compiled_graph_runs_continue_route_end_to_end_with_fakes():
    async def scenario():
        class Inbox:
            def __init__(self):
                self.acks = []

            async def claim_pending(self, root_session_id, limit, *, run_id):
                return [SimpleNamespace(
                    id="event-1",
                    lease_id="lease-1",
                    root_session_id=root_session_id,
                    session_id=root_session_id,
                    type="TEXT_FINAL",
                    source_event="message.part.updated",
                    payload={
                        "type": "TEXT_FINAL",
                        "timestamp": "2026-09-18T10:00:00Z",
                        "payload": {"properties": {"part": {"text": "working"}}},
                    },
                    received_at=datetime.now(timezone.utc),
                    source_run_id=None,
                    sequence=1,
                    ingress_id="ingress-1",
                )]

            async def ack_batch(self, claims):
                self.acks.extend(claims)
                return True

        class Memory:
            async def ensure_session_project(self, root_session_id):
                return "project-1"

            async def get_active_process(self, project_id):
                return {
                    "id": "p1", "project_id": project_id, "key_id": "k1",
                    "name": "process_001", "status": "ACTIVE",
                }

            async def get_process(self, process_id):
                return {
                    "id": process_id, "project_id": "project-1", "key_id": "k1",
                    "name": "process_001", "status": "ACTIVE",
                }

            async def list_processes(self, project_id):
                return [{
                    "id": "p1", "project_id": project_id, "key_id": "k1",
                    "name": "process_001", "status": "ACTIVE",
                }]

            async def get_manifest(self):
                return {"projects": [{"id": "project-1", "keys": [{
                    "id": "k1",
                    "name": "process_001",
                    "categories": [
                        {"id": "strategy-1", "name": "STRATEGY"},
                        {"id": "evidence-1", "name": "EVIDENCE"},
                        {"id": "summary-1", "name": "SUMMARY"},
                    ],
                }]}]}

            async def search(self, query="", **filters):
                return []

            async def neighbors(self, memory_id, **filters):
                return {"nodes": [], "edges": []}

        class Generator:
            async def generate_json(self, **kwargs):
                assert kwargs["operation"] == "EXTRACT_MEMORY_UPDATE"
                return {"memories": [], "relations": []}

        class OpenCode:
            async def send_message(self, session_id, message):
                raise AssertionError("continue route must not intervene")

        sufficient_diagnostics = diagnostics()
        sufficient_diagnostics["context_sufficient_probability"] = {"type": "noul", "noul": 0.9}
        jev = FakeJev([
            {"continuity": {
                "type": "choice",
                "choice": "SAME_PROCESS",
                "probabilities": {"SAME_PROCESS": 0.95, "NEW_PROCESS": 0.05},
                "confidence": 0.9,
            }},
            sufficient_diagnostics,
            {"action": {
                "type": "choice",
                "choice": "CONTINUE",
                "probabilities": {
                    "CONTINUE": 0.9, "NEED_MORE_MEMORY": 0.05,
                    "INTERVENE": 0.03, "CLOSE_PROCESS": 0.02,
                },
                "confidence": 0.91,
            }},
        ])
        inbox = Inbox()
        graph = build_graph(
            inbox=inbox,
            memory=Memory(),
            jev=jev,
            openrouter=Generator(),
            opencode=OpenCode(),
        )
        result = await graph.ainvoke({"root_session_id": "session-1", "original_task": "solve"})
        assert result["final_status"] == "FINALIZED"
        assert result["acknowledged_event_ids"] == ["event-1"]
        assert inbox.acks == [("event-1", "lease-1")]
        assert len(jev.calls) == 3

    asyncio.run(scenario())
