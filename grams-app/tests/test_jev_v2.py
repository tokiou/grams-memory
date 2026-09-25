import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.agent.nodes.assess_process_continuity import make_assess_process_continuity
from supervisor.agent.nodes.supervision_decision import _newest_evidence, _select_intervention_evidence, make_supervision_decision
from supervisor.agent.services.jev_service import JevClient
from supervisor.agent.state_builder import build_jev_process_state


class FakeJev:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    async def system_one(self, *, state, questions):
        self.calls.append((state, questions))
        return {"answers": self.answers.pop(0)}


def test_jev_state_is_canonical_and_supervision_uses_two_calls():
    async def scenario():
        state = {"original_task": "solve", "process_context": {"process": {"id": "p"}, "categories": {}}, "claimed_events": [], "timeout": 99}
        canonical = build_jev_process_state(state)
        assert "timeout" not in canonical
        jev = FakeJev([
            {
                "progress_stall_probability": {"type": "noul", "noul": 0.1},
                "strategy_supported_probability": {"type": "noul", "noul": 0.9},
                "context_sufficient_probability": {"type": "noul", "noul": 0.8},
            },
            {"action": {"type": "choice", "choice": "CONTINUE", "probabilities": {"CONTINUE": 0.9, "NEED_MORE_MEMORY": 0.05, "INTERVENE": 0.03, "CLOSE_PROCESS": 0.02}, "confidence": 0.9}},
        ])
        result = await make_supervision_decision(jev)(state)
        assert result["supervision_decision"]["action"] == "CONTINUE"
        assert result["supervision_diagnostics"]["progress_stall_probability"] == 0.1
        assert len(jev.calls) == 2
        assert jev.calls[0][1]["progress_stall_probability"]["type"] == "noul"
        assert jev.calls[1][1]["action"]["type"] == "choice"

    asyncio.run(scenario())


def test_continuity_is_a_bounded_choice():
    async def scenario():
        jev = FakeJev([{"continuity": {"type": "choice", "choice": "SAME_PROCESS", "probabilities": {"SAME_PROCESS": 0.8, "NEW_PROCESS": 0.2}, "confidence": 0.75}}])
        result = await make_assess_process_continuity(jev)({"process_context": {"process": {}}, "claimed_events": []})
        assert result["process_continuity"]["decision"] == "SAME_PROCESS"
        assert result["process_continuity"]["probabilities"]["NEW_PROCESS"] == 0.2
        assert jev.calls[0][1]["continuity"]["type"] == "choice"

    asyncio.run(scenario())


def test_close_outcome_is_explicit_and_only_outcome_confidence_gates_closing():
    async def scenario():
        diagnostics = {
            "progress_stall_probability": {"type": "noul", "noul": 0.1},
            "strategy_supported_probability": {"type": "noul", "noul": 0.9},
            "context_sufficient_probability": {"type": "noul", "noul": 0.9},
        }
        probabilities = {
            "CONTINUE": 0.05,
            "NEED_MORE_MEMORY": 0.02,
            "INTERVENE": 0.03,
            "CLOSE_PROCESS": 0.9,
        }
        outcomes = {
            "SUCCEEDED": 0.9,
            "FAILED": 0.03,
            "SUPERSEDED": 0.02,
            "ABANDONED": 0.05,
        }
        high = FakeJev([diagnostics, {
            "action": {"type": "choice", "choice": "CLOSE_PROCESS", "probabilities": probabilities, "confidence": 0.9},
            "process_outcome": {"type": "choice", "choice": "SUCCEEDED", "probabilities": outcomes, "confidence": 0.9},
        }])
        result = await make_supervision_decision(high, action_threshold=0.8)({"process_context": {}, "claimed_events": []})
        assert result["supervision_decision"]["process_outcome"] == "SUCCEEDED"
        assert result["supervision_decision"]["process_outcome_probabilities"] == outcomes
        assert result["supervision_decision"]["process_outcome_confidence"] == 0.9

        low = FakeJev([diagnostics, {
            "action": {"type": "choice", "choice": "CLOSE_PROCESS", "probabilities": probabilities, "confidence": 0.2},
            "process_outcome": {"type": "choice", "choice": "SUCCEEDED", "probabilities": outcomes, "confidence": 0.9},
        }])
        result = await make_supervision_decision(low, action_threshold=0.8)({"process_context": {}, "claimed_events": []})
        assert result["supervision_decision"]["action"] == "CLOSE_PROCESS"
        assert result["supervision_decision"]["process_outcome"] == "SUCCEEDED"

        uncertain_outcome = FakeJev([diagnostics, {
            "action": {"type": "choice", "choice": "CLOSE_PROCESS", "probabilities": probabilities, "confidence": 0.9},
            "process_outcome": {"type": "choice", "choice": "SUCCEEDED", "probabilities": outcomes, "confidence": 0.2},
        }])
        result = await make_supervision_decision(
            uncertain_outcome,
            action_threshold=0.8,
            outcome_threshold=0.8,
        )({"process_context": {}, "claimed_events": []})
        assert result["supervision_decision"]["action"] == "CONTINUE"

        invalid_supersession = FakeJev([diagnostics, {
            "action": {"type": "choice", "choice": "CLOSE_PROCESS", "probabilities": probabilities, "confidence": 0.9},
            "process_outcome": {
                "type": "choice",
                "choice": "SUPERSEDED",
                "probabilities": {"SUCCEEDED": 0.02, "FAILED": 0.03, "SUPERSEDED": 0.9, "ABANDONED": 0.05},
                "confidence": 0.9,
            },
        }])
        result = await make_supervision_decision(invalid_supersession)({"process_context": {}, "claimed_events": []})
        assert result["supervision_decision"]["action"] == "CONTINUE"

    asyncio.run(scenario())


def test_explicit_intervention_ignores_confidence_and_context_thresholds():
    async def scenario():
        diagnostics = {
            "progress_stall_probability": {"type": "noul", "noul": 0.8},
            "strategy_supported_probability": {"type": "noul", "noul": 0.2},
            "context_sufficient_probability": {"type": "noul", "noul": 0.3},
        }
        action = {
            "type": "choice",
            "choice": "INTERVENE",
            "probabilities": {
                "CONTINUE": 0.55,
                "NEED_MORE_MEMORY": 0.05,
                "INTERVENE": 0.3,
                "CLOSE_PROCESS": 0.1,
            },
            "confidence": 0.1,
        }
        def choice(value):
            return {"type": "choice", "choice": value, "probabilities": {value: 1.0}, "confidence": 0.9}

        jev = FakeJev([
            diagnostics,
            {"action": action},
            {
                "evidence_memory_1": choice("m1"),
                "evidence_memory_2": choice("NONE"),
                "evidence_memory_3": choice("NONE"),
                "intervention_reason_1": choice("POSSIBLE_PROGRESS_STALL"),
                "intervention_reason_2": choice("NONE"),
            },
        ])
        result = await make_supervision_decision(jev, context_sufficient_threshold=1.0)({
            "process_context": {
                "category_ids": {"EVIDENCE": "evidence-category"},
                "categories": {"EVIDENCE": [{"id": "m1", "category_id": "evidence-category", "title": "Blocker", "content": "No progress"}]},
            },
            "claimed_events": [],
            "memory_expansion_depth": 0,
        })
        assert result["supervision_decision"]["action"] == "INTERVENE"
        assert result["supervision_decision"]["action_confidence"] == 0.1
        assert len(jev.calls) == 3

    asyncio.run(scenario())


def test_low_context_never_promotes_need_more_memory_to_intervention_on_probability_alone():
    async def scenario():
        diagnostics = {
            "progress_stall_probability": {"type": "noul", "noul": 0.8},
            "strategy_supported_probability": {"type": "noul", "noul": 0.2},
            "context_sufficient_probability": {"type": "noul", "noul": 0.2},
        }
        action = {"action": {"type": "choice", "choice": "NEED_MORE_MEMORY", "confidence": 0.92,
                             "probabilities": {"CONTINUE": 0.01, "NEED_MORE_MEMORY": 0.05,
                                               "INTERVENE": 0.92, "CLOSE_PROCESS": 0.02}}}
        jev = FakeJev([diagnostics, action])
        result = await make_supervision_decision(jev)({
            "process_context": {}, "claimed_events": [], "memory_expansion_depth": 3,
        })
        assert result["supervision_decision"]["action"] == "CONTINUE"
        assert len(jev.calls) == 2

    asyncio.run(scenario())


def test_only_exhausted_need_more_memory_uses_intervention_thresholds():
    async def scenario(depth, probability, confidence, max_depth=3):
        diagnostic = {
            "progress_stall_probability": {"type": "noul", "noul": 0.8},
            "strategy_supported_probability": {"type": "noul", "noul": 0.2},
            "context_sufficient_probability": {"type": "noul", "noul": 0.95},
        }
        action = {"action": {"type": "choice", "choice": "NEED_MORE_MEMORY", "confidence": confidence,
                             "probabilities": {"CONTINUE": 0.01, "NEED_MORE_MEMORY": 0.09,
                                               "INTERVENE": probability, "CLOSE_PROCESS": 0.9 - probability}}}
        def choice(value):
            return {"type": "choice", "choice": value, "confidence": 0.9, "probabilities": {value: 1}}
        jev = FakeJev([diagnostic, action, {
            "evidence_memory_1": choice("m1"), "evidence_memory_2": choice("NONE"),
            "evidence_memory_3": choice("NONE"), "intervention_reason_1": choice("REPEATED_FAILURE"),
            "intervention_reason_2": choice("NONE"),
        }])
        result = await make_supervision_decision(jev, max_expansion_depth=max_depth)({
            "memory_expansion_depth": depth,
            "process_context": {"category_ids": {"EVIDENCE": "e1"}, "categories": {"EVIDENCE": [
                {"id": "m1", "category_id": "e1", "title": "Failure", "content": "Repeated failures"},
            ]}}, "claimed_events": [],
        })
        return result["supervision_decision"]["action"], len(jev.calls)

    for depth in (0, 1, 2):
        assert asyncio.run(scenario(depth, 0.8, 0.1)) == ("NEED_MORE_MEMORY", 2)
    assert asyncio.run(scenario(3, 0.8, 0.9)) == ("INTERVENE", 3)
    assert asyncio.run(scenario(3, 0.74, 0.9)) == ("CONTINUE", 2)
    assert asyncio.run(scenario(3, 0.8, 0.1)) == ("CONTINUE", 2)
    assert asyncio.run(scenario(2, 0.8, 0.9, max_depth=2)) == ("CONTINUE", 2)


def test_confident_explicit_intervention_after_retrieval_overrides_low_context():
    async def scenario():
        diagnostics = {
            "progress_stall_probability": {"type": "noul", "noul": 0.8},
            "strategy_supported_probability": {"type": "noul", "noul": 0.2},
            "context_sufficient_probability": {"type": "noul", "noul": 0.25},
        }
        action = {"action": {"type": "choice", "choice": "INTERVENE", "confidence": 0.85,
                             "probabilities": {"CONTINUE": 0.02, "NEED_MORE_MEMORY": 0.06,
                                               "INTERVENE": 0.9, "CLOSE_PROCESS": 0.02}}}
        def choice(value):
            return {"type": "choice", "choice": value, "confidence": 1, "probabilities": {value: 1}}
        selected = {
            "evidence_memory_1": choice("m1"), "evidence_memory_2": choice("NONE"),
            "evidence_memory_3": choice("NONE"), "intervention_reason_1": choice("REPEATED_FAILURE"),
            "intervention_reason_2": choice("NONE"),
        }
        jev = FakeJev([diagnostics, action, selected])
        result = await make_supervision_decision(jev)({
            "process_context": {"category_ids": {"EVIDENCE": "e1"}, "categories": {"EVIDENCE": [
                {"id": "m1", "category_id": "e1", "title": "Failure", "content": "Repeated failures"},
            ]}}, "claimed_events": [], "memory_expansion_depth": 1,
        })
        assert result["supervision_decision"]["action"] == "INTERVENE"
        assert result["supervision_decision"]["evidence_memory_ids"] == ["m1"]
        assert len(jev.calls) == 3

    asyncio.run(scenario())


def test_intervention_deduplicates_selected_evidence_and_reasons_but_rejects_invalid_ids():
    async def scenario(invalid_id=None):
        def choice(value):
            return {"type": "choice", "choice": value, "confidence": 0.8, "probabilities": {value: 1}}

        jev = FakeJev([{
            "progress_stall_probability": {"type": "noul", "noul": 0.7},
            "strategy_supported_probability": {"type": "noul", "noul": 0.2},
            "context_sufficient_probability": {"type": "noul", "noul": 0.1},
        }, {"action": {"type": "choice", "choice": "INTERVENE", "confidence": 0.01,
                       "probabilities": {"CONTINUE": 0.4, "NEED_MORE_MEMORY": 0.2,
                                         "INTERVENE": 0.3, "CLOSE_PROCESS": 0.1}}}, {
            "evidence_memory_1": choice("m1"),
            "evidence_memory_2": choice("m1"),
            "evidence_memory_3": choice(invalid_id or "NONE"),
            "intervention_reason_1": choice("REPEATED_FAILURE"),
            "intervention_reason_2": choice("REPEATED_FAILURE"),
        }])
        state = {
            "process_context": {"category_ids": {"EVIDENCE": "e1"}, "categories": {"EVIDENCE": [
                {"id": "m1", "category_id": "e1", "title": "Failure", "content": "Repeated failures"},
                {"id": "outside", "category_id": "another-category", "title": "Wrong scope"},
            ]}}, "claimed_events": [],
        }
        return await make_supervision_decision(jev)(state)

    result = asyncio.run(scenario())
    assert result["supervision_decision"]["action"] == "INTERVENE"
    assert result["supervision_decision"]["evidence_memory_ids"] == ["m1"]
    assert result["supervision_decision"]["reason_codes"] == ["REPEATED_FAILURE"]
    assert [memory["id"] for memory in result["selected_intervention_memories"]] == ["m1"]

    with pytest.raises(ValueError, match="invalid intervention evidence memory"):
        asyncio.run(scenario("outside"))


def test_intervention_preflights_every_evidence_option_and_keeps_newest_in_scope():
    async def scenario(order):
        requests = []

        def handler(request):
            assert len(request.content) <= 3200
            body = json.loads(request.content)
            requests.append(body)
            choices = {}
            for name in body["questions"]:
                if name.startswith("evidence_memory_"):
                    selected = "m-new" if name.endswith("_1") else "NONE"
                else:
                    selected = "REPEATED_FAILURE" if name.endswith("_1") else "NONE"
                choices[name] = {"type": "choice", "choice": selected,
                                 "probabilities": {selected: 1.0}, "confidence": 0.9}
            return httpx.Response(200, json={"answers": choices})

        evidence = [
            {"id": f"m-{index}", "category_id": "e1", "title": "observation", "content": "x" * 420,
             "updated_at": f"2026-09-01T{index:02}:00:00Z"}
            for index in range(20)
        ]
        evidence.append({"id": "m-new", "category_id": "e1", "content": "recent", "updated_at": "2026-09-24T12:00:00Z"})
        evidence.append({"id": "outside", "category_id": "other", "content": "other process"})
        state = {"process_context": {"category_ids": {"EVIDENCE": "e1"}, "categories": {"EVIDENCE": order(evidence)}},
                 "claimed_events": []}
        original = json.loads(json.dumps(state))
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            client = JevClient(api_key="test", http=http, max_context_tokens=3200)
            ids, reasons, selected = await _select_intervention_evidence(client, state, build_jev_process_state(state))
        finally:
            await http.aclose()
        assert ids == ["m-new"]
        assert reasons == ["REPEATED_FAILURE"]
        assert selected[0]["id"] == "m-new"
        assert state == original
        assert requests
        for body in requests:
            for name, question in body["questions"].items():
                if name.startswith("evidence_memory_"):
                    assert "m-new" in question["criteria"]
                    assert "m-0" not in question["criteria"]
                    assert "outside" not in question["criteria"]
        return [{name: sorted(question["criteria"]) for name, question in body["questions"].items()
                 if name.startswith("evidence_memory_")} for body in requests]

    oldest_first = asyncio.run(scenario(lambda items: items))
    newest_first = asyncio.run(scenario(lambda items: list(reversed(items))))
    assert oldest_first == newest_first


def test_intervention_rejects_pruned_evidence_reference():
    async def scenario():
        def handler(request):
            body = json.loads(request.content)
            return httpx.Response(200, json={"answers": {
                name: {"type": "choice", "choice": "m-old" if name == "evidence_memory_1" else
                       "REPEATED_FAILURE" if name == "intervention_reason_1" else "NONE",
                       "probabilities": {"NONE": 1.0}, "confidence": 0.9}
                for name in body["questions"]
            }})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            client = JevClient(api_key="test", http=http, max_context_tokens=2600)
            state = {"process_context": {"category_ids": {"EVIDENCE": "e1"}, "categories": {"EVIDENCE": [
                {"id": "m-new", "category_id": "e1", "content": "new", "updated_at": "2026-09-24T00:00:00Z"},
                {"id": "m-old", "category_id": "e1", "content": "x" * 1000,
                 "updated_at": "2026-08-01T00:00:00Z"},
                *[{"id": f"m-{i}", "category_id": "e1", "content": "x" * 900,
                   "updated_at": f"2026-09-{i + 1:02}T00:00:00Z"} for i in range(15)],
            ]}}, "claimed_events": []}
            with pytest.raises(ValueError, match="invalid intervention evidence memory"):
                await _select_intervention_evidence(client, state, build_jev_process_state(state))
        finally:
            await http.aclose()

    asyncio.run(scenario())


def test_evidence_without_timestamps_preserves_source_order_for_ties():
    memories = {
        "first": {"id": "first"},
        "second": {"id": "second", "updated_at": "not a timestamp"},
        "recent": {"id": "recent", "updated_at": "2026-09-24T00:00:00Z"},
    }
    assert list(_newest_evidence(memories)) == ["recent", "first", "second"]
    assert list(_newest_evidence(memories)) == ["recent", "first", "second"]
