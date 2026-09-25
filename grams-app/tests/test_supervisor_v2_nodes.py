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
from supervisor.agent.nodes.curate_memory_candidates import make_curate_memory_candidates
from supervisor.agent.nodes.expand_graph import make_expand_graph
from supervisor.agent.nodes.extract_memory_candidates import make_extract_memory_candidates
from supervisor.agent.nodes.finalize_cycle import make_finalize_cycle
from supervisor.agent.nodes.materialize_memories import make_materialize_memories
from supervisor.agent.nodes.record_intervention import make_record_intervention
from supervisor.agent.nodes.send_intervention import make_send_intervention
from supervisor.agent.nodes.supervision_decision import make_supervision_decision
from supervisor.agent.graph import build_graph
from supervisor.agent.prompts import format_intervention_for_agent
from supervisor.agent.runtime import SupervisorRuntime
from supervisor.agent.services.openrouter_service import OpenRouterClient
from supervisor.agent.schemas import (
    validate_materializations,
    validate_memory_candidates,
    validate_memory_proposal,
    validate_summary,
)
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
                operation="EXTRACT_MEMORY_CANDIDATES",
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


def test_memory_candidates_require_claimed_event_provenance_and_materializations_are_strict():
    events = [{"id": "event-1", "type": "TOOL_RESULT_FINAL"}]
    candidate = {
        "candidate_ref": "new_1",
        "fact": "The focused validation failed.",
        "evidence": [{"event_id": "event-1", "excerpt": "exit code 1"}],
        "provenance": {
            "source_event_ids": ["event-1"],
            "source_event_types": ["TOOL_RESULT_FINAL"],
            "cycle_id": "cycle-1",
        },
    }
    assert validate_memory_candidates({"candidates": [candidate]}, events)[0]["candidate_ref"] == "new_1"
    with pytest.raises(ValueError, match="claimed event"):
        validate_memory_candidates({"candidates": [{
            **candidate,
            "evidence": [{"event_id": "foreign", "excerpt": "not claimed"}],
        }]}, events)
    with pytest.raises(ValueError, match="not kept"):
        validate_materializations({"memories": [{
            "candidate_ref": "new_1", "title": "title", "content": "content", "status": "ACTIVE",
        }]}, {"new_1"})
    assert validate_memory_proposal({
        "memories": [{"category": "EVIDENCE", "title": "kept", "content": "kept", "candidate_ref": "new_2"}],
        "relations": [],
    })["memories"][0]["candidate_ref"] == "new_2"


def test_extract_curate_and_materialize_memory_pipeline_keeps_jev_metadata_authoritative():
    async def scenario():
        candidate = {
            "candidate_ref": "new_1",
            "fact": "The focused validation failed.",
            "evidence": [{"event_id": "event-1", "excerpt": "exit code 1"}],
            "provenance": {
                "source_event_ids": ["event-1"],
                "source_event_types": ["TOOL_RESULT_FINAL"],
                "cycle_id": "cycle-1",
            },
        }

        class Generator:
            def __init__(self):
                self.operations = []

            async def generate_json(self, **kwargs):
                self.operations.append(kwargs["operation"])
                if kwargs["operation"] == "EXTRACT_MEMORY_CANDIDATES":
                    return {"candidates": [candidate]}
                return {"memories": [{"candidate_ref": "new_1", "title": "Validation failed", "content": "The focused validation failed."}]}

        def choice(value):
            return {"type": "choice", "choice": value, "probabilities": {value: 1.0}, "confidence": 0.9}

        class Curator:
            async def system_one(self, *, state, questions):
                assert state["memory_candidates"] == [candidate]
                return {"answers": {
                    "new_1_keep": choice("KEEP"),
                    "new_1_category": choice("EVIDENCE"),
                    "new_1_role": choice("RESULT"),
                    "new_1_status": choice("FAILED"),
                    "new_1_progress": choice("NEGATIVE"),
                    "new_1_importance": choice("HIGH"),
                    "new_1_relation_target": choice("NONE"),
                    "new_1_relation_type": choice("NONE"),
                }}

        state = {
            "original_task": "solve",
            "claimed_events": [{"id": "event-1", "type": "TOOL_RESULT_FINAL", "cycle_id": "cycle-1"}],
            "process_context": {
                "process": {"id": "p1"},
                "categories": {"STRATEGY": [], "EVIDENCE": []},
                "relations": [],
            },
        }
        generator = Generator()
        extracted = await make_extract_memory_candidates(generator)(state)
        state.update(extracted)
        state.update(await make_curate_memory_candidates(Curator())(state))
        state.update(await make_materialize_memories(generator)(state))
        assert generator.operations == ["EXTRACT_MEMORY_CANDIDATES", "MATERIALIZE_MEMORIES"]
        memory = state["proposed_memory_update"]["memories"][0]
        assert memory["category"] == "EVIDENCE"
        assert memory["role"] == "RESULT"
        assert memory["status"] == "FAILED"
        assert memory["confidence"] == 0.9
        assert memory["importance"] == 0.9
        assert state["proposed_memory_update"]["relations"] == []

    asyncio.run(scenario())


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


def test_jev_state_compaction_preserves_recent_memory():
    state = {
        "evidence": [
            {"id": "newest", "content": "x" * 500},
            {"id": "middle", "content": "y" * 500},
            {"id": "oldest", "content": "z" * 500},
        ],
    }
    compacted = compact_jev_state(state, max_tokens=700)

    assert [item["id"] for item in compacted["evidence"]] == ["newest"]


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
        with pytest.raises(ValueError, match="Jev question 'decision' cannot fit"):
            await client.system_one(
                state={"task": {"objective": "solve"}},
                questions={"decision": {"type": "noul", "instructions": "x" * 200}},
            )
        await http.aclose()
        assert calls == 0

    asyncio.run(scenario())


def test_jev_client_compacts_long_objective_before_splitting_questions():
    async def scenario():
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"answers": {}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = JevClient(api_key="test-key", http=http, max_context_tokens=500)
        await client.system_one(
                state={"task": {"objective": "x" * 1000}},
                questions={
                    "first": {"type": "noul", "instructions": "first"},
                    "second": {"type": "noul", "instructions": "second"},
                },
        )
        await http.aclose()
        assert calls >= 1

    asyncio.run(scenario())


def test_jev_client_compacts_long_candidates_and_keeps_addressed_candidate():
    async def scenario():
        bodies = []

        def handler(request):
            assert len(request.content) <= 850
            body = json.loads(request.content)
            bodies.append(body)
            return httpx.Response(200, json={"answers": {name: {"value": "KEEP"} for name in body["questions"]}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = JevClient(api_key="test-key", http=http, max_context_tokens=850)
        result = await client.system_one(
            state={"memory_candidates": [
                {"candidate_ref": "new_1", "fact": "a" * 2500},
                {"candidate_ref": "new_2", "fact": "b" * 2500},
            ]},
            questions={"new_1_keep": {"type": "choice", "criteria": {"KEEP": "yes", "DROP": "no"}}},
        )
        await http.aclose()
        assert result["answers"]["new_1_keep"]["value"] == "KEEP"
        assert len(bodies) == 1
        assert [item["candidate_ref"] for item in bodies[0]["state"]["memory_candidates"]] == ["new_1"]
        assert bodies[0]["state"]["context_compaction"]["truncated"] is True

    asyncio.run(scenario())


def test_jev_split_relation_target_retains_all_candidate_choices_in_context():
    async def scenario():
        seen = []

        def handler(request):
            assert len(request.content) <= 700
            body = json.loads(request.content)
            seen.append(body)
            return httpx.Response(200, json={"answers": {name: {"value": "NONE"} for name in body["questions"]}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = JevClient(api_key="test-key", http=http, max_context_tokens=700)
        questions = {
            "new_1_keep": {"type": "choice", "criteria": {"KEEP": "yes", "DROP": "no"}, "instructions": "k" * 270},
            "new_1_relation_target": {"type": "choice", "criteria": {
                "NONE": "nothing", "new_2": "the other candidate",
            }, "instructions": "r" * 270},
        }
        await client.system_one(state={"memory_candidates": [
            {"candidate_ref": "new_1", "fact": "primary"},
            {"candidate_ref": "new_2", "fact": "related fact"},
        ]}, questions=questions)
        await http.aclose()
        relation_requests = [body for body in seen if "new_1_relation_target" in body["questions"]]
        assert relation_requests
        assert len(seen) == 2
        assert all({item["candidate_ref"] for item in body["state"]["memory_candidates"]} == {"new_1", "new_2"}
                   for body in relation_requests)

    asyncio.run(scenario())


def test_jev_client_splits_oversized_question_sets_without_dropping_questions():
    async def scenario():
        requests = []

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            return httpx.Response(200, json={
                "answers": {name: {"value": name} for name in body["questions"]},
                "usage": {"input_tokens": 10, "details": {"cached_tokens": 2}},
                "batch_marker": len(requests),
                **{f"batch_{name}": name for name in body["questions"]},
            })

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = JevClient(api_key="test-key", http=http, max_context_tokens=260)
        state = {"task": {"objective": "solve"}}
        original_state = json.loads(json.dumps(state))
        questions = {
            "first": {
                "type": "choice",
                "criteria": {"YES": "accept", "NO": "reject"},
                "instructions": "x" * 60,
            },
            "second": {
                "type": "choice",
                "criteria": {"YES": "accept", "NO": "reject"},
                "instructions": "y" * 60,
            },
        }

        result = await client.system_one(state=state, questions=questions)
        await http.aclose()

        assert len(requests) == 2
        assert all(len(json.dumps(body, separators=(",", ":")).encode()) <= 260 for body in requests)
        assert {
            name: question
            for body in requests
            for name, question in body["questions"].items()
        } == questions
        assert result["answers"] == {"first": {"value": "first"}, "second": {"value": "second"}}
        assert result["usage"] == {"input_tokens": 20, "details": {"cached_tokens": 4}}
        assert result["batch_marker"] == 2
        assert result["batch_first"] == "first"
        assert result["batch_second"] == "second"
        assert state == original_state

    asyncio.run(scenario())


def test_jev_client_rejects_missing_answers_after_question_splitting():
    async def scenario():
        def handler(request):
            body = json.loads(request.content)
            name = next(iter(body["questions"]))
            answers = {} if name == "second" else {name: {"value": name}}
            return httpx.Response(200, json={"answers": answers})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = JevClient(api_key="test-key", http=http, max_context_tokens=260)
        with pytest.raises(RuntimeError, match="incomplete answers for split question batches"):
            await client.system_one(
                state={"task": {"objective": "solve"}},
                questions={
                    "first": {"type": "noul", "instructions": "x" * 100},
                    "second": {"type": "noul", "instructions": "y" * 100},
                },
            )
        await http.aclose()

    asyncio.run(scenario())


def test_jev_client_compacts_progressively_after_provider_size_rejection():
    async def scenario():
        requests = []

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                return httpx.Response(400, text="maximum context length exceeded")
            if len(requests) < 3:
                return httpx.Response(413, text="request payload too large")
            return httpx.Response(200, json={"answers": {"decision": {"value": "continue"}}})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = JevClient(api_key="test-key", http=http, max_context_tokens=2000)
        state = {
            "recent_execution": [
                {"id": str(index), "result": "x" * 180}
                for index in range(8)
            ],
        }
        original_state = json.loads(json.dumps(state))
        result = await client.system_one(
            state=state,
            questions={"decision": {"type": "noul", "instructions": "continue?"}},
        )
        await http.aclose()

        assert len(requests) == 3
        request_sizes = [len(json.dumps(body, separators=(",", ":")).encode()) for body in requests]
        assert request_sizes[0] > request_sizes[1] > request_sizes[2]
        assert all(size <= 2000 for size in request_sizes)
        assert result["answers"]["decision"]["value"] == "continue"
        assert state == original_state

    asyncio.run(scenario())


def test_jev_client_splits_questions_after_size_rejection_when_state_cannot_shrink():
    async def scenario():
        requests = []

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(body["questions"]) > 1:
                return httpx.Response(413, text="request payload too large")
            return httpx.Response(200, json={
                "answers": {name: {"value": name} for name in body["questions"]},
            })

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = JevClient(api_key="test-key", http=http, max_context_tokens=1000)
        result = await client.system_one(
            state={"task": {"objective": "solve"}},
            questions={
                "first": {"type": "noul", "instructions": "first"},
                "second": {"type": "noul", "instructions": "second"},
            },
        )
        await http.aclose()

        assert len(requests) == 3
        assert len(requests[0]["questions"]) == 2
        assert all(len(body["questions"]) == 1 for body in requests[1:])
        assert result["answers"] == {"first": {"value": "first"}, "second": {"value": "second"}}

    asyncio.run(scenario())


def test_jev_client_limits_context_retries_and_does_not_retry_other_http_errors():
    async def scenario():
        requests = []

        def handler(request):
            requests.append(request.content)
            return httpx.Response(413, text="payload too large")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = JevClient(api_key="test-key", http=http, max_context_tokens=10000)
        state = {
            "recent_execution": [
                {"id": str(index), "result": "x" * 180}
                for index in range(50)
            ],
        }
        with pytest.raises(httpx.HTTPStatusError):
            await client.system_one(
                state=state,
                questions={"decision": {"type": "noul", "instructions": "continue?"}},
            )
        await http.aclose()
        assert len(requests) == 4  # initial request plus three compacted retries

        requests.clear()

        def invalid_request_handler(request):
            requests.append(request.content)
            return httpx.Response(400, text="input tokens field is invalid")

        http = httpx.AsyncClient(transport=httpx.MockTransport(invalid_request_handler))
        client = JevClient(api_key="test-key", http=http, max_context_tokens=1000)
        with pytest.raises(httpx.HTTPStatusError):
            await client.system_one(
                state={"task": {"objective": "solve"}},
                questions={"decision": {"type": "noul", "instructions": "continue?"}},
            )
        await http.aclose()
        assert len(requests) == 1

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


def test_apply_memory_update_persists_curated_metadata_in_mcp_compatible_payload():
    async def scenario():
        class Memory:
            def __init__(self):
                self.created = []

            async def create(self, value):
                self.created.append(value)
                return {
                    "id": "m-curated",
                    "category_id": value["category_id"],
                    **value,
                }

        memory = Memory()
        result = await make_apply_memory_update(memory)({
            "claimed_events": [{"id": "event-1", "cycle_id": "cycle-curated"}],
            "process_context": {
                "category_ids": {"STRATEGY": "cat-s", "EVIDENCE": "cat-e"},
                "categories": {"STRATEGY": [], "EVIDENCE": []},
                "relations": [],
            },
            "proposed_memory_update": {
                "memories": [{
                    "category": "EVIDENCE",
                    "title": "Validation failed",
                    "content": "The focused validation failed.",
                    "candidate_ref": "new_1",
                    "role": "RESULT",
                    "status": "FAILED",
                    "confidence": 0.9,
                    "progress_effect": "NEGATIVE",
                    "importance": 0.9,
                    "provenance": {
                        "source_event_ids": ["event-1"],
                        "source_event_types": ["TOOL_RESULT_FINAL"],
                        "cycle_id": "cycle-curated",
                    },
                }],
                "relations": [],
            },
        })
        payload = memory.created[0]
        assert payload["type"] == "RESULT"
        assert payload["status"] == "FAILED"
        assert payload["confidence"] == 0.9
        assert payload["description"].splitlines()[0] == "cycle-curated:new_1"
        envelope = json.loads(payload["description"].splitlines()[1].removeprefix("GRAMS_CURATION_V1:"))
        assert envelope["progress_effect"] == "NEGATIVE"
        assert envelope["provenance"]["source_event_ids"] == ["event-1"]
        assert result["memory_update_result"]["created_memory_ids"] == ["m-curated"]

    asyncio.run(scenario())


def test_apply_memory_update_reuses_existing_fact_without_overwriting_curation():
    async def scenario():
        class Memory:
            def __init__(self):
                self.created = []

            async def create(self, payload):
                self.created.append(payload)
                return {"id": "new-memory", **payload}

        memory = Memory()
        stored = {
            "id": "stored", "category_id": "cat-e", "title": "Repeated error",
            "content": "Same failure recurred", "description": "GRAMS_CURATION_V1:{}",
        }
        candidate = {
            "category": "EVIDENCE", "title": "Repeated error", "content": "Same failure recurred",
            "candidate_ref": "new_1", "role": "RESULT", "status": "VALIDATED",
            "confidence": 0.7, "progress_effect": "NEGATIVE", "importance": 0.5,
            "provenance": {"source_event_ids": ["new-event"]},
        }
        result = await make_apply_memory_update(memory)({
            "claimed_events": [{"id": "new-event", "cycle_id": "new-cycle"}],
            "process_context": {"category_ids": {"STRATEGY": "cat-s", "EVIDENCE": "cat-e"},
                                "categories": {"STRATEGY": [], "EVIDENCE": [stored]}, "relations": []},
            "proposed_memory_update": {"memories": [candidate], "relations": []},
        })
        assert memory.created == []
        assert result["memory_update_result"]["resolved_refs"] == {"new_1": "stored"}
        assert stored["description"] == "GRAMS_CURATION_V1:{}"

    asyncio.run(scenario())


def test_apply_memory_update_replay_uses_durable_candidate_ref_not_shared_event_provenance():
    async def scenario():
        class Memory:
            def __init__(self):
                self.created = []

            async def create(self, payload):
                self.created.append(payload)
                return {"id": "new-memory", **payload}

        memory = Memory()
        stored = {
            "id": "stored", "category_id": "cat-e", "title": "Old phrasing", "content": "Old fact",
            "description": "cycle-1:new_1\nGRAMS_CURATION_V1:{\"version\":1,\"provenance\":{\"source_event_ids\":[\"event-1\"]}}",
        }
        def candidate(ref, title):
            return {"category": "EVIDENCE", "title": title, "content": title,
                    "candidate_ref": ref, "role": "RESULT", "status": "VALIDATED",
                    "confidence": 0.8, "progress_effect": "NEUTRAL", "importance": 0.5,
                    "provenance": {"source_event_ids": ["event-1"]}}

        result = await make_apply_memory_update(memory)({
            "claimed_events": [{"id": "event-1", "cycle_id": "cycle-1"}],
            "process_context": {"category_ids": {"STRATEGY": "cat-s", "EVIDENCE": "cat-e"},
                                "categories": {"STRATEGY": [], "EVIDENCE": [stored]}, "relations": []},
            "proposed_memory_update": {"memories": [candidate("new_1", "New phrasing"),
                                                     candidate("new_2", "Different fact")], "relations": []},
        })
        assert result["memory_update_result"]["resolved_refs"] == {"new_1": "stored", "new_2": "new-memory"}
        assert len(memory.created) == 1
        assert memory.created[0]["title"] == "Different fact"

    asyncio.run(scenario())


def test_apply_memory_update_replay_same_candidate_ref_ignores_changed_phrasing_and_metadata():
    async def scenario():
        class Memory:
            def __init__(self):
                self.created = []

            async def create(self, payload):
                self.created.append(payload)
                return {"id": "unexpected-new", **payload}

        memory = Memory()
        durable_description = (
            'cycle-1:new_1\nGRAMS_CURATION_V1:{"version":1,"candidate_ref":"new_1",'
            '"role":"RESULT","status":"VALIDATED","confidence":0.8,'
            '"progress_effect":"NEGATIVE","importance":0.9,'
            '"provenance":{"source_event_ids":["event-1"]}}'
        )
        stored = {
            "id": "stored", "category_id": "cat-e", "title": "Original title",
            "content": "Original durable fact", "description": durable_description,
        }
        changed_proposal = {
            "category": "EVIDENCE", "title": "Regenerated wording", "content": "Rephrased fact",
            "candidate_ref": "new_1", "role": "ERROR", "status": "REJECTED",
            "confidence": 0.2, "progress_effect": "POSITIVE", "importance": 0.1,
            "provenance": {"source_event_ids": ["event-1"], "note": "changed replay metadata"},
        }
        result = await make_apply_memory_update(memory)({
            "claimed_events": [{"id": "event-1", "cycle_id": "cycle-1"}],
            "process_context": {
                "category_ids": {"STRATEGY": "cat-s", "EVIDENCE": "cat-e"},
                "categories": {"STRATEGY": [], "EVIDENCE": [stored]}, "relations": [],
            },
            "proposed_memory_update": {"memories": [changed_proposal], "relations": []},
        })

        assert result["memory_update_result"]["resolved_refs"] == {"new_1": "stored"}
        assert memory.created == []
        assert stored["title"] == "Original title"
        assert stored["content"] == "Original durable fact"
        assert stored["description"] == durable_description

    asyncio.run(scenario())


def test_apply_memory_update_rejects_incomplete_curated_relation_response():
    async def scenario():
        class Memory:
            def __init__(self):
                self.created = 0

            async def create(self, value):
                self.created += 1
                return {"id": f"memory-{self.created}", **value}

            async def link(self, *args, **kwargs):
                return {"id": "edge-1"}

        def curated(category, ref):
            return {
                "category": category,
                "title": ref,
                "content": ref,
                "candidate_ref": ref,
                "role": "RESULT",
                "status": "FAILED",
                "confidence": 0.9,
                "progress_effect": "NEGATIVE",
                "importance": 0.9,
                "provenance": {
                    "source_event_ids": ["event-1"],
                    "source_event_types": ["TOOL_RESULT_FINAL"],
                    "cycle_id": None,
                },
            }

        with pytest.raises(RuntimeError, match="incomplete curated relation"):
            await make_apply_memory_update(Memory())({
                "process_context": {
                    "category_ids": {"STRATEGY": "cat-s", "EVIDENCE": "cat-e"},
                    "categories": {"STRATEGY": [], "EVIDENCE": []},
                    "relations": [],
                },
                "proposed_memory_update": {
                    "memories": [curated("EVIDENCE", "new_1"), curated("STRATEGY", "new_2")],
                    "relations": [{
                        "source_id": "new_1",
                        "relation_type": "SUPPORTS",
                        "target_id": "new_2",
                        "confidence": 0.9,
                        "evidence_strength": "STRONG",
                        "direct": True,
                    }],
                },
            })

    asyncio.run(scenario())


def test_materialize_memories_sends_only_kept_curation_decisions():
    async def scenario():
        class Generator:
            async def generate_json(self, **kwargs):
                assert [item["candidate_ref"] for item in kwargs["payload"]["memory_candidates"]] == ["new_1"]
                assert [item["candidate_ref"] for item in kwargs["payload"]["memory_curation"]["decisions"]] == ["new_1"]
                return {"memories": [{"candidate_ref": "new_1", "title": "Kept", "content": "Kept fact"}]}

        candidates = [{
            "candidate_ref": "new_1", "fact": "kept", "evidence": [{"event_id": "e1", "excerpt": "kept"}],
            "provenance": {"source_event_ids": ["e1"], "source_event_types": ["TEXT_FINAL"], "cycle_id": "c1"},
        }, {
            "candidate_ref": "new_2", "fact": "dropped", "evidence": [{"event_id": "e1", "excerpt": "dropped"}],
            "provenance": {"source_event_ids": ["e1"], "source_event_types": ["TEXT_FINAL"], "cycle_id": "c1"},
        }]
        result = await make_materialize_memories(Generator())({
            "memory_candidates": candidates,
            "memory_curation": {"decisions": [
                {"candidate_ref": "new_1", "keep": True, "category": "EVIDENCE", "role": "RESULT", "status": "VALIDATED", "confidence": 0.9, "progress_effect": "POSITIVE", "importance": 0.9},
                {"candidate_ref": "new_2", "keep": False, "category": "EVIDENCE", "role": "ERROR", "status": "FAILED", "confidence": 0.8, "progress_effect": "NEGATIVE", "importance": 0.5},
            ], "relations": []},
        })
        assert result["proposed_memory_update"]["memories"][0]["candidate_ref"] == "new_1"

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
                assert kwargs["payload"]["evidence_memory_ids"] == ["m-evidence"]
                assert kwargs["payload"]["reason_codes"] == ["POSSIBLE_PROGRESS_STALL"]
                assert [item["id"] for item in kwargs["payload"]["selected_memories"]] == ["m-evidence"]
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
                assert value["description"].startswith("GRAMS_INTERVENTION_V1:")
                return {"ID": "audit-1", "CategoryID": "evidence-category", **value}

            async def update(self, memory_id, value):
                assert memory_id == "audit-1"
                return {"ID": memory_id, "CategoryID": "evidence-category", **value}

        state = {
            "root_session_id": "session-1",
            "claimed_events": [{"id": "event-1", "lease_id": "lease-1"}],
            "supervision_decision": {
                "action": "INTERVENE",
                "evidence_memory_ids": ["m-evidence"],
                "reason_codes": ["POSSIBLE_PROGRESS_STALL"],
            },
            "supervision_diagnostics": diagnostics(),
            "process_context": {
                "process": {"id": "p1"},
                "categories": {
                    "EVIDENCE": [{
                        "id": "m-evidence",
                        "category_id": "evidence-category",
                        "title": "Progress blocker",
                        "content": "No artifact exists yet.",
                    }],
                },
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


def test_build_intervention_rejects_unallowlisted_reason_without_model_call():
    async def scenario():
        class Generator:
            def __init__(self):
                self.calls = 0

            async def generate_text(self, **kwargs):
                self.calls += 1
                return "unexpected"

        generator = Generator()
        with pytest.raises(ValueError, match="unsupported reason code"):
            await make_build_intervention(generator)({
                "supervision_decision": {
                    "action": "INTERVENE",
                    "evidence_memory_ids": ["m1"],
                    "reason_codes": ["NOT_ALLOWLISTED"],
                },
                "process_context": {
                    "categories": {"EVIDENCE": [{"id": "m1", "category_id": "cat-e"}]},
                    "category_ids": {"EVIDENCE": "cat-e"},
                },
            })
        assert generator.calls == 0

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
                assert kwargs["operation"] == "EXTRACT_MEMORY_CANDIDATES"
                return {"candidates": []}

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
