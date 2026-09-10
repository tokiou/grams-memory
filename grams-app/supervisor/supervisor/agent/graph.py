"""LangGraph topology for the Supervisor."""

from typing import Any
import logging

from langgraph.graph import END, START, StateGraph

from ..config import Config
from ..inbox import EventInbox
from ..memory import MemoryClient
from ..opencode import OpenCodeClient
from .nodes.intervene import make_intervene_node
from .nodes.memory_operation import make_memory_operation_node
from .nodes.read_inbox import make_read_inbox_node
from .nodes.review import ReviewModel, ReviewService, make_review_node
from .state import SupervisorState
from ..observability import begin_node, bind_correlation, elapsed_ms, emit, finish_node, monotonic_ns

logger = logging.getLogger(__name__)


def _instrument_node(name: str, node):
    async def instrumented(state: SupervisorState) -> dict[str, Any]:
        started = monotonic_ns()
        step, previous_node, gap_ms = begin_node(name)
        fields = {
            "node": name,
            "run_id": state.get("run_id"),
            "root_session_id": state.get("root_session_id"),
            "thread_id": f"{state.get('supervisor_checkpoint_id', '')}:{state.get('root_session_id', '')}",
            "current_event_count": len(state.get("current_events", [])),
            "graph_step": step,
            "previous_node": previous_node,
            "since_previous_node_ms": gap_ms,
        }
        emit(logger, logging.DEBUG, "node_started", **fields)
        try:
            with bind_correlation(node=name, graph_step=step):
                result = await node(state)
        except Exception as error:
            emit(logger, logging.ERROR, "node_failed", **fields, error=type(error).__name__,
                 detail=str(error), node_duration_ms=elapsed_ms(started))
            raise
        emit(logger, logging.DEBUG, "node_completed", **fields,
             node_duration_ms=finish_node(name, started), next_action=result.get("next_action"))
        return result

    return instrumented


def build_graph(
    inbox: EventInbox,
    memory: MemoryClient,
    opencode: OpenCodeClient,
    checkpointer: Any,
    *,
    config: Config,
    review_model: ReviewModel | None = None,
    memory_category_id: str | None = None,
    memory_manifest: dict[str, Any] | None = None,
):
    graph = StateGraph(SupervisorState)
    review_service = ReviewService(
        memory,
        stagnation_enabled=config.stagnation_enabled,
        stagnation_threshold_seconds=config.stagnation_threshold_seconds,
    )
    graph.add_node("REVIEW", _instrument_node("REVIEW", make_review_node(
        inbox, opencode, review_service, review_model,
        memory_manifest=memory_manifest, memory_category_id=memory_category_id,
    )))
    graph.add_node("READ_INBOX", _instrument_node("READ_INBOX", make_read_inbox_node(limit=config.inbox_batch_size)))
    graph.add_node("MEMORY_OPERATION", _instrument_node("MEMORY_OPERATION", make_memory_operation_node(memory, memory_category_id)))
    graph.add_node("INTERVENE", _instrument_node("INTERVENE", make_intervene_node(opencode)))
    graph.add_edge(START, "REVIEW")
    graph.add_conditional_edges(
        "REVIEW",
        lambda state: state.get("next_action", "DONE"),
        {
            "READ_INBOX": "READ_INBOX",
            "MEMORY_OPERATION": "MEMORY_OPERATION",
            "INTERVENE": "INTERVENE",
            "DONE": END,
        },
    )
    graph.add_edge("READ_INBOX", "REVIEW")
    graph.add_edge("MEMORY_OPERATION", "REVIEW")
    graph.add_edge("INTERVENE", "REVIEW")
    return graph.compile(checkpointer=checkpointer)
