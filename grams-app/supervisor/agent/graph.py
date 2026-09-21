"""LangGraph wiring for the v2 Supervisor."""

from langgraph.graph import END, START, StateGraph

from supervisor.agent.nodes.apply_memory_update import make_apply_memory_update
from supervisor.agent.nodes.assess_agent_health import make_assess_agent_health
from supervisor.agent.nodes.assess_process_continuity import make_assess_process_continuity
from supervisor.agent.nodes.build_intervention import make_build_intervention
from supervisor.agent.nodes.close_current_process import make_close_current_process
from supervisor.agent.nodes.ensure_active_process import make_ensure_active_process
from supervisor.agent.nodes.expand_graph import make_expand_graph
from supervisor.agent.nodes.extract_memory_update import make_extract_memory_update
from supervisor.agent.nodes.finalize_cycle import make_finalize_cycle
from supervisor.agent.nodes.load_process_context import make_load_process_context
from supervisor.agent.nodes.read_inbox import make_read_inbox
from supervisor.agent.nodes.record_intervention import make_record_intervention
from supervisor.agent.nodes.send_intervention import make_send_intervention
from supervisor.agent.nodes.start_new_process import make_start_new_process
from supervisor.agent.nodes.supervision_decision import make_supervision_decision
from supervisor.agent.nodes.write_process_summary import make_write_process_summary
from supervisor.agent.services.process_service import ProcessService
from supervisor.agent.state import SupervisorState


def build_graph(
    *,
    inbox,
    memory,
    jev,
    openrouter,
    opencode,
    interventions=None,
    intervention_fallback="system_transform",
    process_service=None,
    batch_size=20,
    run_id=None,
    max_expansion_depth=3,
    checkpointer=None,
):
    if max_expansion_depth > 3:
        raise ValueError("max_expansion_depth cannot exceed 3")
    if jev is None:
        raise ValueError("build_graph requires a Jev client")
    if openrouter is None:
        raise ValueError("build_graph requires an OpenRouter client")
    if opencode is None:
        raise ValueError("build_graph requires an OpenCode client")
    process_service = process_service or ProcessService(memory)

    graph = StateGraph(SupervisorState)
    graph.add_node("READ_INBOX", make_read_inbox(inbox, batch_size=batch_size, run_id=run_id))
    graph.add_node("ASSESS_AGENT_HEALTH", make_assess_agent_health())
    graph.add_node("ENSURE_ACTIVE_PROCESS", make_ensure_active_process(process_service))
    graph.add_node("LOAD_PROCESS_CONTEXT", make_load_process_context(memory))
    graph.add_node("ASSESS_PROCESS_CONTINUITY", make_assess_process_continuity(jev))
    graph.add_node("EXTRACT_MEMORY_UPDATE", make_extract_memory_update(openrouter))
    graph.add_node("APPLY_MEMORY_UPDATE", make_apply_memory_update(memory))
    graph.add_node("SUPERVISION_DECISION", make_supervision_decision(jev))
    graph.add_node("EXPAND_GRAPH", make_expand_graph(memory, max_depth=max_expansion_depth))
    graph.add_node("WRITE_PROCESS_SUMMARY", make_write_process_summary(openrouter))
    graph.add_node("CLOSE_CURRENT_PROCESS", make_close_current_process(process_service, memory))
    graph.add_node("START_NEW_PROCESS", make_start_new_process(process_service))
    graph.add_node("BUILD_INTERVENTION", make_build_intervention(openrouter))
    graph.add_node(
        "SEND_INTERVENTION",
        make_send_intervention(
            opencode,
            memory,
            pending_interventions=interventions,
            fallback_mode=intervention_fallback,
        ),
    )
    graph.add_node("RECORD_INTERVENTION", make_record_intervention(memory))
    graph.add_node("FINALIZE", make_finalize_cycle(inbox))

    graph.add_edge(START, "READ_INBOX")
    graph.add_conditional_edges(
        "READ_INBOX",
        lambda state: "events" if state.get("claimed_events") else "empty",
        {"events": "ASSESS_AGENT_HEALTH", "empty": END},
    )
    graph.add_conditional_edges(
        "ASSESS_AGENT_HEALTH",
        lambda state: state.get("health_route", "normal"),
        {"heartbeat": "FINALIZE", "recover": "SEND_INTERVENTION", "normal": "ENSURE_ACTIVE_PROCESS"},
    )
    graph.add_conditional_edges(
        "ENSURE_ACTIVE_PROCESS",
        lambda state: "complete" if state.get("cycle_already_completed") else "active",
        {"complete": "FINALIZE", "active": "LOAD_PROCESS_CONTEXT"},
    )
    graph.add_conditional_edges(
        "LOAD_PROCESS_CONTEXT",
        lambda state: state["context_route"],
        {
            "assess": "ASSESS_PROCESS_CONTINUITY",
            "extract": "EXTRACT_MEMORY_UPDATE",
            "supervise": "SUPERVISION_DECISION",
        },
    )
    graph.add_conditional_edges(
        "ASSESS_PROCESS_CONTINUITY",
        lambda state: state["process_continuity"]["decision"],
        {"SAME_PROCESS": "EXTRACT_MEMORY_UPDATE", "NEW_PROCESS": "WRITE_PROCESS_SUMMARY"},
    )
    graph.add_edge("EXTRACT_MEMORY_UPDATE", "APPLY_MEMORY_UPDATE")
    graph.add_edge("APPLY_MEMORY_UPDATE", "LOAD_PROCESS_CONTEXT")
    graph.add_conditional_edges(
        "SUPERVISION_DECISION",
        lambda state: state["supervision_decision"]["action"],
        {
            "CONTINUE": "FINALIZE",
            "NEED_MORE_MEMORY": "EXPAND_GRAPH",
            "INTERVENE": "BUILD_INTERVENTION",
            "CLOSE_PROCESS": "WRITE_PROCESS_SUMMARY",
        },
    )
    graph.add_conditional_edges(
        "EXPAND_GRAPH",
        lambda state: "exhausted" if state.get("memory_expansion_exhausted") else "expanded",
        {"expanded": "SUPERVISION_DECISION", "exhausted": "FINALIZE"},
    )
    graph.add_edge("BUILD_INTERVENTION", "SEND_INTERVENTION")
    graph.add_edge("SEND_INTERVENTION", "RECORD_INTERVENTION")
    graph.add_edge("RECORD_INTERVENTION", "FINALIZE")
    graph.add_edge("WRITE_PROCESS_SUMMARY", "CLOSE_CURRENT_PROCESS")
    graph.add_conditional_edges(
        "CLOSE_CURRENT_PROCESS",
        lambda state: "new" if state.get("process_continuity", {}).get("decision") == "NEW_PROCESS" else "final",
        {"new": "START_NEW_PROCESS", "final": "FINALIZE"},
    )
    graph.add_edge("START_NEW_PROCESS", "LOAD_PROCESS_CONTEXT")
    graph.add_edge("FINALIZE", END)
    return graph.compile(checkpointer=checkpointer)
