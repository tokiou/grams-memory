"""Progress-stall detection node stub."""

from supervisor.agent.state import SupervisorState


async def detect_progress_stall(state: SupervisorState) -> dict:
    """Detect the operational signal POSSIBLE_PROGRESS_STALL.

    Future implementation: use objective activity, evidence, validation, and
    strategy-persistence signals. It must not infer causes, create additional
    detectors, decide intervention, or use remaining Harbor timeout. It should
    preferably remain deterministic and non-LLM.
    """
    raise NotImplementedError
