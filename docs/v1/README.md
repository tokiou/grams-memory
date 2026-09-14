# GRAMS v1 Architecture

GRAMS v1 is a Supervisor-managed relational execution memory system for
long-horizon agents. OpenCode remains the Action Agent. The Supervisor observes
its execution, maintains process memory, and makes memory-conditioned decisions.

## System Flow

```text
OpenCode
   |
   v
OpenCode plugin
   |
   | normalized events
   v
FastAPI receiver
   |
   v
SQLite Inbox
   |
   v
Supervisor Runtime
   |
   v
LangGraph
   |
   +--> read current process graph from Memory MCP
   +--> update strategy and evidence memory
   +--> review accumulated evidence
   +--> continue, expand, intervene, or close
```

The receiver and Inbox are the durable transport layer. LangGraph is an
orchestration layer over that transport; it is not the event store.

## Durable Event Boundary

The receiver writes every accepted event to the SQLite table
`supervisor_events` before returning `202`.

The Inbox owns:

- event status transitions;
- leases;
- retry scheduling;
- acknowledgement;
- recovery after process interruption.

A future graph consumer must use this contract:

```text
claim_pending -> PROCESSING -> ack -> PROCESSED
                              \-> fail -> PENDING or FAILED
```

An event may be represented as a Python object while a graph invocation is
running, but it must not be copied into a second durable state store.

## Process Memory

Each supervised task maps to one Project. Each meaningful strategy or line of
work maps to one process Key. A process is not a command, tool call, or single
reasoning message.

Each process has this fixed ontology:

```text
process_N
├── strategy
├── evidence
└── summary
```

`strategy` records plans, attempts, decisions, pivots, and interventions.
`evidence` records discoveries, errors, measurements, validations, and failed
assumptions. `summary` records the decision-relevant outcome when a process is
closed or superseded.

The Supervisor must preserve relations such as:

```text
strategy_A FAILED_BECAUSE evidence_X
strategy_B SUPERSEDES strategy_A
strategy_B PRODUCED result_Y
result_Y VALIDATES strategy_B
```

## Review Context

Every meaningful review requires both:

```text
recent execution
+
current process graph
```

Normal reviews use a compact Level 0 process index. The Supervisor may expand
the active process or related process neighborhood when the current evidence
is insufficient.

Operational signals such as these trigger deeper inspection, not an automatic
intervention:

- `POSSIBLE_PROGRESS_STALL`;
- `POSSIBLE_RESEARCH_LOOP`;
- `POSSIBLE_HYPOTHESIS_OSCILLATION`;
- `DELIVERABLE_MISSING`;
- `VALIDATION_MISSING`.

Elapsed time may require another review, but time alone never justifies an
intervention.

## Process Lifecycle

```text
ACTIVE
  -> SUCCEEDED
  -> FAILED
  -> SUPERSEDED
  -> ABANDONED
```

A process remains active while the agent pursues substantially the same
strategy. It closes only when accumulated evidence supports a terminal result.
Closing a process writes or updates its Summary. Starting a new process keeps a
relation to the previous process.

## Component Boundary

Retained transport and integrations:

- `grams-opencode/opencode_plugin/`;
- `grams-app/supervisor/supervisor/api/`;
- `grams-app/supervisor/supervisor/inbox/`;
- `grams-app/supervisor/supervisor/memory/`;
- `grams-app/supervisor/supervisor/opencode/`;
- `grams-app/supervisor/supervisor/platform/sqlite/`;
- `grams-app/memory-mcp/`.

To be rebuilt as v1 orchestration:

- Supervisor Runtime;
- LangGraph graph and state;
- process-memory update flow;
- graph-context retrieval;
- review policy;
- intervention delivery policy;
- process closing and next-process creation.
