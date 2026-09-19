from __future__ import annotations

from supervisor.agent.prompts import INTERVENTION_SYSTEM_PROMPT
from supervisor.agent.state_builder import build_jev_process_state


def make_build_intervention(openrouter):
    async def node(state):
        if state.get("supervision_decision", {}).get("action") != "INTERVENE":
            raise ValueError("BUILD_INTERVENTION requires an INTERVENE decision")
        message = await openrouter.generate_text(
            operation="BUILD_INTERVENTION",
            payload={
                "state": build_jev_process_state(state),
                "diagnostics": state.get("supervision_diagnostics") or {},
            },
            system_prompt=INTERVENTION_SYSTEM_PROMPT,
        )
        if not isinstance(message, str) or not message.strip():
            raise ValueError("OpenRouter returned an empty intervention")
        return {"intervention_message": message.strip()}

    return node
