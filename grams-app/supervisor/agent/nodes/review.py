"""Supervisor review node stub."""

from supervisor.agent.state import SupervisorState


async def review(state: SupervisorState) -> dict:
    """Decide whether accumulated evidence justifies continuing the process.

    Future implementation: use the current process context, recent events,
    progress signal, and expanded context to produce CONTINUE,
    NEED_MORE_MEMORY, INTERVENE, or CLOSE_PROCESS. It must not call tools,
    modify memory, send messages, or solve the Action Agent's task.
    """
    raise NotImplementedError
