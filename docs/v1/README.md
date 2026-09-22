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

The receiver and Inbox are the durable transport layer. The Supervisor worker
claims Inbox events and runs LangGraph cycles over that transport. LangGraph is
an orchestration layer, not the event store; SQLite remains the durable source
of truth.

## Model Responsibilities

The complete v2 model boundary and target graph are documented in
[`docs/v2/JEV_OPENROUTER.md`](../v2/JEV_OPENROUTER.md).

GRAMS uses two model services with deliberately separate responsibilities.

### Jev: Structured Decisions

Jev is used for typed, bounded decisions that influence routing:

- process continuity: `SAME_PROCESS` or `NEW_PROCESS`;
- review action: `CONTINUE`, `NEED_MORE_MEMORY`, `INTERVENE`, or
  `CLOSE_PROCESS`;
- progress/blockage and evidence-relevance scores;
- duplicate and retrieval-priority classification.

Jev does not write Memory MCP, operate the Inbox, call OpenCode, or generate
durable text. LangGraph and deterministic Python code remain responsible for
interpreting confidence, enforcing thresholds, and performing side effects.

### Generative LLM: Text Only

DeepSeek through OpenRouter is reserved for text generation:

- memory titles and STRATEGY/EVIDENCE content;
- the dynamic message delivered during an intervention.

The generative model does not choose graph routes or call tools. Its output is
validated by deterministic code before it can affect Memory MCP or OpenCode.

## Durable Event Boundary

The receiver writes every accepted event to the SQLite table
`supervisor_events` before returning `202`.

The Inbox owns:

- event status transitions;
- leases;
- retry scheduling;
- acknowledgement;
- recovery after process interruption.

The Supervisor worker and runtime consume events through this contract:

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

## Implemented Components

The repository includes the durable transport, integrations, and Supervisor
orchestration:

- `grams-opencode/opencode_plugin/` — event normalization and delivery;
- `grams-app/supervisor/api/` — event and intervention HTTP endpoints;
- `grams-app/supervisor/inbox/` — durable event journal, claims, leases,
  retries, and acknowledgements;
- `grams-app/supervisor/agent/` — graph, state, runtime, background worker,
  decision and memory-update nodes;
- `grams-app/supervisor/memory/` — Python Memory MCP client;
- `grams-app/supervisor/opencode/` — OpenCode control and context client;
- `grams-app/supervisor/platform/sqlite/` — Supervisor database setup;
- `grams-app/memory-mcp/` — standalone Go graph-memory MCP server.

The flow and process-memory principles in this document describe the intended
behavioral contract. For implementation details and tested edge cases, use the
runtime code, the focused tests under `grams-app/tests/`, and the v2 design in
[`../v2/JEV_OPENROUTER.md`](../v2/JEV_OPENROUTER.md).
