from __future__ import annotations
from supervisor.agent.prompts import MEMORY_UPDATE_SYSTEM_PROMPT
from supervisor.agent.schemas import MEMORY_UPDATE_JSON_SCHEMA, validate_memory_proposal
from supervisor.agent.state_builder import build_jev_process_state

def make_extract_memory_update(openrouter):
    async def node(state):
        value = await openrouter.generate_json(
            operation="EXTRACT_MEMORY_UPDATE",
            payload=build_jev_process_state(state),
            system_prompt=MEMORY_UPDATE_SYSTEM_PROMPT,
            schema=MEMORY_UPDATE_JSON_SCHEMA,
        )
        return {"proposed_memory_update": validate_memory_proposal(value)}
    return node
