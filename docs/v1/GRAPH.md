# GRAMS - Initial Supervisor Implementation Specification

## Objective

This document defines what must be implemented to rebuild the first
process-based version of the GRAMS Supervisor from scratch.

At this stage, the real logic of most nodes is intentionally not implemented.
The current incremental implementation includes the Inbox claim boundary in
`read_inbox`; the remaining nodes are still stubs. The following must exist:

- `graph.py` with the LangGraph flow;
- `state.py` with the shared state contract;
- `schemas.py` with the main structured outputs;
- one file per node under `agent/nodes/`;
- each function signature and a functional docstring describing its contract;
- `prompts.py` with the base prompts for LLM-backed nodes.

Each node must initially be a stub:

```python
async def some_node(state: SupervisorState) -> dict:
    """Complete functional description of the node."""
    raise NotImplementedError
```

Do not implement real integration with Memory MCP, OpenCode, or an LLM
provider yet. `read_inbox` is the deliberate exception: it may claim events
from the existing Inbox, but it must not interpret or acknowledge them.

## 1. Design Principles

### 1.1 One Supervisor

GRAMS has one logical Supervisor. Do not create independent agents such as a
Memory Agent, Review Agent, or Progress Agent.

Nodes that need reasoning may use different prompts, but they belong to the
same Supervisor and share `SupervisorState`.

### 1.2 LangGraph Controls the Flow

The LLM must not freely choose which tool to execute or whether to query
memory. LangGraph controls the flow:

```text
LangGraph controls the flow.
The LLM interprets.
Memory MCP persists.
OpenCode executes.
```

### 1.3 Current Process Memory Is Mandatory

Before every `REVIEW`, the Supervisor must load an up-to-date representation of
the active process graph.

There must be no valid path such as:

```text
recent events -> REVIEW
```

without first passing through `LOAD_PROCESS_CONTEXT`.

### 1.4 Durable Memory Lives in MCP

`SupervisorState` must not become a second semantic memory. The following must
not be stored as the source of truth in state:

- `known_errors`;
- `failed_approaches`;
- `current_hypotheses`;
- `validated_results`;
- `process_history`;
- `memory_manifest`;
- `semantic_progress_snapshot`.

That knowledge belongs in Memory MCP. State contains only identity, cycle
coordination, temporary graph snapshots, and transient node outputs.

## 2. File Organization

```text
grams-app/supervisor/agent/
├── graph.py
├── state.py
├── schemas.py
├── prompts.py
└── nodes/
    ├── __init__.py
    ├── read_inbox.py
    ├── ensure_active_process.py
    ├── load_process_context.py
    ├── assess_process_continuity.py
    ├── extract_memory_update.py
    ├── apply_memory_update.py
    ├── detect_progress_stall.py
    ├── review.py
    ├── expand_graph.py
    ├── send_intervention.py
    ├── record_intervention.py
    ├── write_process_summary.py
    ├── close_current_process.py
    ├── start_new_process.py
    └── finalize_cycle.py
```

Do not add nodes, agents, categories, or signals without discussing them first.

## 3. Functional Memory Model

A Key represents one coherent execution process. Examples:

```text
process_001_python_bruteforce
process_002_compiled_search
process_003_validate_keys
```

Each process uses only these categories:

```text
STRATEGY
EVIDENCE
SUMMARY
```

`STRATEGY` describes what the Action Agent is trying to do: plans, approaches,
decisions, pivots, and meaningful attempts.

`EVIDENCE` describes what execution demonstrated: errors, discoveries, results,
measurements, validations, or evidence for and against the strategy.

`SUMMARY` is a compressed description of a closed process. It preserves the
strategy, decisive evidence, outcome, reason for success, failure, or
supersession, and reusable knowledge.

## 4. Main Flow

```text
START
  |
  v
READ_INBOX
  |
  v
ENSURE_ACTIVE_PROCESS
  |
  v
LOAD_PROCESS_CONTEXT_BEFORE_UPDATE
  |
  v
ASSESS_PROCESS_CONTINUITY
  |
  +-- SAME_PROCESS
  |      |
  |      v
  |   EXTRACT_MEMORY_UPDATE
  |      |
  |      v
  |   APPLY_MEMORY_UPDATE
  |      |
  |      v
  |   LOAD_PROCESS_CONTEXT_AFTER_UPDATE
  |      |
  |      v
  |   DETECT_PROGRESS_STALL
  |      |
  |      v
  |    REVIEW
  |      |
  |      +-- CONTINUE ----------------------------+
  |      |                                        |
  |      +-- NEED_MORE_MEMORY                     |
  |      |      |                                 |
  |      |      v                                 |
  |      |   EXPAND_GRAPH                         |
  |      |      |                                 |
  |      |      +-------------> REVIEW             |
  |      |                                        |
  |      +-- INTERVENE                            |
  |      |      |                                 |
  |      |      v                                 |
  |      |   SEND_INTERVENTION                    |
  |      |      |                                 |
  |      |      v                                 |
  |      |   RECORD_INTERVENTION -----------------+
  |      |                                        |
  |      +-- CLOSE_PROCESS                        |
  |             |                                 |
  |             v                                 |
  |        WRITE_PROCESS_SUMMARY                  |
  |             |                                 |
  |             v                                 |
  |        CLOSE_CURRENT_PROCESS -----------------+
  |
  +-- NEW_PROCESS
         |
         v
    WRITE_PROCESS_SUMMARY
         |
         v
    CLOSE_CURRENT_PROCESS
         |
         v
    START_NEW_PROCESS
         |
         v
    LOAD_NEW_PROCESS_CONTEXT
         |
         v
    EXTRACT_MEMORY_UPDATE
```

## 5. `graph.py`

`graph.py` must contain only imports, routers, `StateGraph` construction, node
registration, edges, conditional edges, and compilation. It must not contain
node implementations.

Expected imports:

```python
from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph

from supervisor.agent.state import SupervisorState
from supervisor.agent.nodes.read_inbox import make_read_inbox_node
from supervisor.agent.nodes.ensure_active_process import ensure_active_process
from supervisor.agent.nodes.load_process_context import load_process_context
from supervisor.agent.nodes.assess_process_continuity import assess_process_continuity
from supervisor.agent.nodes.extract_memory_update import extract_memory_update
from supervisor.agent.nodes.apply_memory_update import apply_memory_update
from supervisor.agent.nodes.detect_progress_stall import detect_progress_stall
from supervisor.agent.nodes.review import review
from supervisor.agent.nodes.expand_graph import expand_graph
from supervisor.agent.nodes.send_intervention import send_intervention
from supervisor.agent.nodes.record_intervention import record_intervention
from supervisor.agent.nodes.write_process_summary import write_process_summary
from supervisor.agent.nodes.close_current_process import close_current_process
from supervisor.agent.nodes.start_new_process import start_new_process
from supervisor.agent.nodes.finalize_cycle import finalize_cycle
```

### Routers

```python
def route_after_read_inbox(
    state: SupervisorState,
) -> Literal["HAS_EVENTS", "NO_EVENTS"]:
    """Decide whether a new event batch exists."""
    return "HAS_EVENTS" if state.get("claimed_events") else "NO_EVENTS"


def route_after_process_continuity(
    state: SupervisorState,
) -> Literal["SAME_PROCESS", "NEW_PROCESS"]:
    """Route according to the process-continuity decision."""
    return state["process_continuity"]["decision"]


def route_after_review(
    state: SupervisorState,
) -> Literal["CONTINUE", "NEED_MORE_MEMORY", "INTERVENE", "CLOSE_PROCESS"]:
    """Route according to the structured REVIEW decision."""
    return state["review_decision"]["action"]


def route_after_close_process(
    state: SupervisorState,
) -> Literal["START_NEW_PROCESS", "FINISH_CYCLE"]:
    """Decide whether a pivot requires a successor process."""
    if state.get("pending_process_transition"):
        return "START_NEW_PROCESS"
    return "FINISH_CYCLE"
```

### Builder Invariants

```python
def build_graph(*, inbox: EventInbox, checkpointer=None):
    """Build the main GRAMS Supervisor graph."""
```

The builder must enforce:

1. New events enter only through `READ_INBOX`.
2. An ACTIVE process exists before events are interpreted.
3. The current process is loaded before continuity is assessed.
4. The LLM only proposes memory changes.
5. A deterministic node applies those changes to MCP.
6. The process is loaded again after memory changes.
7. `REVIEW` never operates on stale process memory.
8. There is only one operational symptom: `POSSIBLE_PROGRESS_STALL`.
9. A progress stall never implies automatic intervention.
10. Every intervention passes through `REVIEW`.
11. A successor process is created only after a real trajectory pivot.

The expected node registrations and edges are:

```python
graph = StateGraph(SupervisorState)

    graph.add_node("read_inbox", make_read_inbox_node(inbox))
graph.add_node("ensure_active_process", ensure_active_process)
graph.add_node("load_process_context_before_update", load_process_context)
graph.add_node("assess_process_continuity", assess_process_continuity)
graph.add_node("extract_memory_update", extract_memory_update)
graph.add_node("apply_memory_update", apply_memory_update)
graph.add_node("load_process_context_after_update", load_process_context)
graph.add_node("detect_progress_stall", detect_progress_stall)
graph.add_node("review", review)
graph.add_node("expand_graph", expand_graph)
graph.add_node("send_intervention", send_intervention)
graph.add_node("record_intervention", record_intervention)
graph.add_node("write_process_summary", write_process_summary)
graph.add_node("close_current_process", close_current_process)
graph.add_node("start_new_process", start_new_process)
graph.add_node("load_new_process_context", load_process_context)
graph.add_node("finalize_cycle", finalize_cycle)
```

The compiled graph must implement these transitions:

```text
START -> read_inbox
read_inbox -> ensure_active_process | END
ensure_active_process -> load_process_context_before_update
load_process_context_before_update -> assess_process_continuity
assess_process_continuity -> extract_memory_update | write_process_summary
extract_memory_update -> apply_memory_update
apply_memory_update -> load_process_context_after_update
load_process_context_after_update -> detect_progress_stall
detect_progress_stall -> review
review -> finalize_cycle | expand_graph | send_intervention | write_process_summary
expand_graph -> review
send_intervention -> record_intervention -> finalize_cycle
write_process_summary -> close_current_process
close_current_process -> start_new_process | finalize_cycle
start_new_process -> load_new_process_context -> extract_memory_update
finalize_cycle -> END
```

## 6. `state.py`

Keep state small. Semantic history belongs in Memory MCP.

```python
from __future__ import annotations

from typing import Any, TypedDict


class SupervisorState(TypedDict, total=False):
    root_session_id: str
    project_id: str
    original_task: str

    claimed_events: list[ClaimedInboxEvent]

    active_process_id: str
    process_context: dict[str, Any]

    process_continuity: ProcessContinuity
    pending_process_transition: dict[str, Any]

    proposed_memory_update: dict[str, Any]
    memory_update_result: dict[str, Any]

    progress_stall: ProgressStall
    expanded_memory_context: dict[str, Any]
    memory_expansion_depth: int
    review_decision: ReviewDecision
    pending_process_summary: str
    intervention_result: dict[str, Any]
    cycle_errors: list[dict[str, Any]]
```

Field meanings:

- `root_session_id`: identifies the supervised OpenCode execution;
- `project_id`: scopes the memory graph;
- `original_task`: global task objective;
- `claimed_events`: current-cycle event envelopes, including IDs and leases for
  later ACK;
- `active_process_id`: reference to the supervised process;
- `process_context`: temporary snapshot derived from MCP;
- `process_continuity`: SAME_PROCESS or NEW_PROCESS output;
- `pending_process_transition`: temporary successor-process data;
- `proposed_memory_update`: LLM proposal separated from persistence;
- `memory_update_result`: applied, rejected, or failed writes;
- `progress_stall`: the single operational signal;
- `expanded_memory_context`: deeper graph context requested by REVIEW;
- `memory_expansion_depth`: guard against expansion cycles;
- `review_decision`: contract between REVIEW and deterministic routers;
- `pending_process_summary`: generated summary waiting for persistence;
- `intervention_result`: delivery result separated from the decision to intervene;
- `cycle_errors`: operational diagnostics, not semantic task memory.

Do not add semantic caches such as known errors, failed approaches, hypotheses,
validated results, or process history to this state.

## 7. `schemas.py`

Define structured output contracts without implementing parsing or model
integration yet. At minimum define:

- `ProcessContinuityDecision`;
- `MemoryCandidate`;
- `RelationCandidate`;
- `MemoryUpdateProposal`;
- `ReviewDecision`;
- `ProcessSummary`;
- `ProgressStall`.

Conceptual example:

```python
class MemoryCandidate(TypedDict):
    category: Literal["STRATEGY", "EVIDENCE"]
    title: str
    content: str


class RelationCandidate(TypedDict):
    source_id: str
    relation_type: str
    target_id: str


class MemoryUpdateProposal(TypedDict):
    memories: list[MemoryCandidate]
    relations: list[RelationCandidate]
```

Do not define the final temporary-ID strategy until that technical decision is
made.

## 8. Node Stubs

Every node must contain only its signature, basic typing, a complete functional
docstring, and `raise NotImplementedError`.

### `read_inbox.py`

Claims a batch from Inbox and stores serializable event envelopes as
`claimed_events`, including event IDs and leases for later ACK. It does not
interpret events, call an LLM, consult memory, detect progress, or decide
interventions.

### `ensure_active_process.py`

Reuses an existing ACTIVE process or creates the first process through Memory
MCP. It does not detect pivots, assess strategy quality, or use an LLM.

### `load_process_context.py`

Loads process ID, name, status, STRATEGY, EVIDENCE, relevant relations, SUMMARY,
and recent relevant changes from Memory MCP. It is reused before continuity
assessment, after memory updates, and after a new process starts.

### `assess_process_continuity.py`

Uses recent events and process context to classify only SAME_PROCESS or
NEW_PROCESS. It does not judge quality, intervene, write memory, or create the
successor process.

### `extract_memory_update.py`

Identifies new decision-relevant STRATEGY, EVIDENCE, and relation proposals. It
avoids duplicates and trivial activity. It does not create SUMMARY, decide
continuation, intervene, or write directly to MCP.

### `apply_memory_update.py`

Validates and applies the proposal. It enforces categories, scope, process IDs,
relation types, IDs, and obvious duplicates. It is deterministic and does not
use an LLM.

### `detect_progress_stall.py`

Detects only `POSSIBLE_PROGRESS_STALL` using objective signals such as duration,
new evidence, validation, results, activity, and strategy persistence. It does
not determine causes, create additional detectors, decide intervention, or use
Harbor's remaining timeout.

### `review.py`

Uses the current process context, recent events, progress signal, and expanded
context to decide only CONTINUE, NEED_MORE_MEMORY, INTERVENE, or CLOSE_PROCESS.
It does not call tools, modify memory, send messages, or solve the task.

### `expand_graph.py`

Retrieves the memories, relations, neighbors, summaries, and related processes
requested by REVIEW. It does not use an LLM and updates expansion depth.

### `send_intervention.py`

Delivers the intervention already selected by REVIEW using the retained
OpenCode client. It does not decide again whether to intervene.

### `record_intervention.py`

Records the reason, evidence, guidance, and delivery result in Memory MCP. It
does not use an LLM.

### `write_process_summary.py`

Uses an LLM to create a compact summary preserving strategy, decisive evidence,
outcome, cause, and reusable knowledge. It does not write directly to MCP.

### `close_current_process.py`

Persists the pending summary and closes the process with SUCCEEDED, FAILED,
SUPERSEDED, or ABANDONED. It is deterministic and does not use an LLM.

### `start_new_process.py`

Creates a successor after a real pivot using `pending_process_transition` and
preserves a relation such as `SUPERSEDES`. It does not invent a pivot or use an
LLM.

### `finalize_cycle.py`

Eventually ACKs the claimed event IDs, persists required operational state, and
clears transient cycle fields. `END` means only that the batch finished, not
that the task finished.

## 9. Prompts

Declare four prompts without model calls:

- `PROCESS_CONTINUITY_SYSTEM_PROMPT`: classify SAME_PROCESS versus NEW_PROCESS;
- `MEMORY_UPDATE_SYSTEM_PROMPT`: propose STRATEGY, EVIDENCE, and relations;
- `REVIEW_SYSTEM_PROMPT`: decide continuation, expansion, intervention, or closure;
- `PROCESS_SUMMARY_SYSTEM_PROMPT`: compress a closing process into SUMMARY.

## 10. Future Dependencies

| Node | LLM | Future dependency |
| --- | --- | --- |
| READ_INBOX | No | Inbox |
| ENSURE_ACTIVE_PROCESS | No | Memory MCP lifecycle |
| LOAD_PROCESS_CONTEXT | No | Memory MCP read |
| ASSESS_PROCESS_CONTINUITY | Yes | None |
| EXTRACT_MEMORY_UPDATE | Yes | None |
| APPLY_MEMORY_UPDATE | No | Memory MCP write |
| DETECT_PROGRESS_STALL | No | Operational state/telemetry |
| REVIEW | Yes | None |
| EXPAND_GRAPH | No | Memory MCP read/traversal |
| SEND_INTERVENTION | No initially | OpenCode client |
| RECORD_INTERVENTION | No | Memory MCP write |
| WRITE_PROCESS_SUMMARY | Yes | None |
| CLOSE_CURRENT_PROCESS | No | Memory MCP lifecycle |
| START_NEW_PROCESS | No | Memory MCP lifecycle |
| FINALIZE_CYCLE | No | Inbox |

The deliberate separation is:

```text
LLM:
proposes, classifies, evaluates, summarizes

Deterministic nodes:
read, validate, persist, send, update lifecycle
```

## 11. Do Not Implement Yet

Do not implement in this stage:

- real timer logic;
- a concrete progress-stall threshold;
- a final definition of meaningful progress;
- LLM provider integration;
- concrete LangChain structured-output parsing;
- retries or fallbacks;
- HTTP calls to MCP;
- SQLite queries or Inbox repository logic;
- OpenCode HTTP calls;
- the final relation enum;
- semantic deduplication;
- graph ranking;
- graph-expansion policy;
- the final expansion-depth limit;
- final intervention formatting;
- definitive first-process naming;
- real process-transition heuristics;
- final state cleanup rules.

Each item will be implemented and reviewed node by node later.

## 12. Completion Criteria

This stage is complete when:

- `graph.py` represents exactly the defined flow;
- `state.py` contains the initial state contract;
- `schemas.py` contains the base types;
- every node file exists;
- every node contains only typing, a functional docstring, and
  `raise NotImplementedError`;
- `prompts.py` contains the four prompts;
- no business logic is implemented;
- the package imports without avoidable circular dependencies;
- no additional nodes, signals, categories, or agents were added.

## 13. Final Principle

```text
Inbox
= what just happened

Memory MCP
= what we know about the execution

LangGraph State
= where we are in the procedure

Supervisor LLM
= what the evidence means

OpenCode
= who solves the task
```

The main invariant is:

> The Supervisor never evaluates process continuity while ignoring the memory
> graph of the process it is supervising.
