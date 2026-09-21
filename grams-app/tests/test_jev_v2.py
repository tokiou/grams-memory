import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.agent.nodes.assess_process_continuity import make_assess_process_continuity
from supervisor.agent.nodes.supervision_decision import make_supervision_decision
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


def test_close_outcome_is_explicit_and_low_confidence_actions_fall_back_to_continue():
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
        assert result["supervision_decision"]["action"] == "CONTINUE"
        assert "process_outcome" not in result["supervision_decision"]

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


def test_exhausted_memory_expansion_falls_back_to_high_probability_intervention():
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
                "CONTINUE": 0.05,
                "NEED_MORE_MEMORY": 0.05,
                "INTERVENE": 0.8,
                "CLOSE_PROCESS": 0.1,
            },
            "confidence": 0.8,
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
        result = await make_supervision_decision(jev)({
            "process_context": {
                "category_ids": {"EVIDENCE": "evidence-category"},
                "categories": {"EVIDENCE": [{"id": "m1", "category_id": "evidence-category", "title": "Blocker", "content": "No progress"}]},
            },
            "claimed_events": [],
            "memory_expansion_depth": 3,
        })
        assert result["supervision_decision"]["action"] == "INTERVENE"

    asyncio.run(scenario())
