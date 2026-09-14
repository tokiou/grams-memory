"""Process-continuity assessment node stub."""

from supervisor.agent.state import SupervisorState


async def assess_process_continuity(state: SupervisorState) -> dict:
    """Classify new events as SAME_PROCESS or NEW_PROCESS.

    Future implementation: use recent events and the current process context to
    identify a real strategic pivot. It must not judge quality, intervene,
    write memory, or create the successor process. It uses an LLM with a
    dedicated structured output.
    """
    raise NotImplementedError
