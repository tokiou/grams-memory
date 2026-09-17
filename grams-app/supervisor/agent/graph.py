"""LangGraph topology for the GRAMS Supervisor skeleton."""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from supervisor.inbox import EventInbox
from supervisor.memory.client import MemoryClient
from supervisor.agent.nodes.apply_memory_update import apply_memory_update
from supervisor.agent.nodes.assess_process_continuity import assess_process_continuity
from supervisor.agent.nodes.close_current_process import make_close_current_process
from supervisor.agent.nodes.detect_progress_stall import detect_progress_stall
from supervisor.agent.nodes.ensure_active_process import make_ensure_active_process
from supervisor.agent.nodes.expand_graph import expand_graph
from supervisor.agent.nodes.extract_memory_update import extract_memory_update
from supervisor.agent.nodes.finalize_cycle import finalize_cycle
from supervisor.agent.nodes.load_process_context import make_load_process_context
from supervisor.agent.nodes.read_inbox import make_read_inbox_node
from supervisor.agent.nodes.record_intervention import record_intervention
from supervisor.agent.nodes.review import review
from supervisor.agent.nodes.send_intervention import send_intervention
from supervisor.agent.nodes.start_new_process import make_start_new_process
from supervisor.agent.nodes.write_process_summary import write_process_summary
from supervisor.agent.services.process_service import ProcessService
from supervisor.agent.state import SupervisorState


def route_after_read_inbox(state: SupervisorState) -> Literal["HAS_EVENTS", "NO_EVENTS"]:
    """Route based on whether READ_INBOX claimed a new event batch."""
    return "HAS_EVENTS" if state.get("claimed_events") else "NO_EVENTS"


def route_after_process_continuity(
    state: SupervisorState,
) -> Literal["SAME_PROCESS", "NEW_PROCESS"]:
    """Route according to the structured process-continuity output."""
    return state["process_continuity"]["decision"]


def route_after_review(
    state: SupervisorState,
) -> Literal["CONTINUE", "NEED_MORE_MEMORY", "INTERVENE", "CLOSE_PROCESS"]:
    """Route according to the structured REVIEW decision."""
    return state["review_decision"]["action"]


def route_after_close_process(
    state: SupervisorState,
) -> Literal["START_NEW_PROCESS", "FINISH_CYCLE"]:
    """Route to a successor process only when a transition is pending."""
    return "START_NEW_PROCESS" if state.get("pending_process_transition") else "FINISH_CYCLE"


def build_graph(
    *,
    inbox: EventInbox,
    memory: MemoryClient,
    batch_size: int = 20,
    run_id: str | None = None,
    checkpointer=None,
):
    """Build and compile the initial GRAMS Supervisor graph skeleton.

    Most node implementations remain stubs. The topology enforces that events
    are read first, an active process exists, process context is loaded before
    continuity/review, memory updates are applied before the second context
    load, and review decisions route deterministically.
    """
    graph = StateGraph(SupervisorState)

    graph.add_node("read_inbox", make_read_inbox_node(inbox, batch_size=batch_size, run_id=run_id))
    process_service = ProcessService(memory)
    load_context = make_load_process_context(memory)
    graph.add_node("ensure_active_process", make_ensure_active_process(process_service))
    graph.add_node("load_process_context_before_update", load_context)
    graph.add_node("assess_process_continuity", assess_process_continuity)
    graph.add_node("extract_memory_update", extract_memory_update)
    graph.add_node("apply_memory_update", apply_memory_update)
    graph.add_node("load_process_context_after_update", load_context)
    graph.add_node("detect_progress_stall", detect_progress_stall)
    graph.add_node("review", review)
    graph.add_node("expand_graph", expand_graph)
    graph.add_node("send_intervention", send_intervention)
    graph.add_node("record_intervention", record_intervention)
    graph.add_node("write_process_summary", write_process_summary)
    graph.add_node("close_current_process", make_close_current_process(process_service))
    graph.add_node("start_new_process", make_start_new_process(process_service))
    graph.add_node("load_new_process_context", load_context)
    graph.add_node("finalize_cycle", finalize_cycle)

    graph.add_edge(START, "read_inbox")
    graph.add_conditional_edges(
        "read_inbox",
        route_after_read_inbox,
        {"HAS_EVENTS": "ensure_active_process", "NO_EVENTS": END},
    )
    graph.add_edge("ensure_active_process", "load_process_context_before_update")
    graph.add_edge("load_process_context_before_update", "assess_process_continuity")
    graph.add_conditional_edges(
        "assess_process_continuity",
        route_after_process_continuity,
        {"SAME_PROCESS": "extract_memory_update", "NEW_PROCESS": "write_process_summary"},
    )
    graph.add_edge("extract_memory_update", "apply_memory_update")
    graph.add_edge("apply_memory_update", "load_process_context_after_update")
    graph.add_edge("load_process_context_after_update", "detect_progress_stall")
    graph.add_edge("detect_progress_stall", "review")
    graph.add_conditional_edges(
        "review",
        route_after_review,
        {
            "CONTINUE": "finalize_cycle",
            "NEED_MORE_MEMORY": "expand_graph",
            "INTERVENE": "send_intervention",
            "CLOSE_PROCESS": "write_process_summary",
        },
    )
    graph.add_edge("expand_graph", "review")
    graph.add_edge("send_intervention", "record_intervention")
    graph.add_edge("record_intervention", "finalize_cycle")
    graph.add_edge("write_process_summary", "close_current_process")
    graph.add_conditional_edges(
        "close_current_process",
        route_after_close_process,
        {"START_NEW_PROCESS": "start_new_process", "FINISH_CYCLE": "finalize_cycle"},
    )
    graph.add_edge("start_new_process", "load_new_process_context")
    graph.add_edge("load_new_process_context", "extract_memory_update")
    graph.add_edge("finalize_cycle", END)

    return graph.compile(checkpointer=checkpointer)
