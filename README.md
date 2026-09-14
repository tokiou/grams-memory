GRAMS

GRAMS is a supervisor-managed relational execution memory system for long-horizon agents.

The Action Agent (currently OpenCode) remains focused on solving the task. A separate Supervisor observes its execution, organizes important execution knowledge into a graph-backed Memory MCP, and uses that memory to decide whether the agent should continue, reconsider, or receive an intervention.

The central design principle is:

The Supervisor must not review the current execution while ignoring the memory of the process it is supervising.

Every meaningful review is therefore conditioned on an up-to-date view of the current process graph.

Architecture

OpenCode
   |
   | normalized execution events
   v
OpenCode plugin
   |
   | POST /events
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
   +-----------------------------+
   |                             |
   | every meaningful REVIEW     | memory writes / graph updates
   v                             v
Current Process Context <---- Memory MCP
   |                             |
   | compact graph view          | SQLite graph memory
   v                             |
Supervisor REVIEW ---------------+
   |
   +--> SILENT / CONTINUE
   |
   +--> EXPAND MEMORY
   |
   +--> INTERVENE --> OpenCode
   |
   +--> CLOSE PROCESS
            |
            v
         SUMMARY
            |
            v
      START NEXT PROCESS

The Action Agent does not manage its own long-term memory.

The Supervisor owns the memory lifecycle:

observe execution
    ->
identify the active process
    ->
store strategy and evidence
    ->
create typed relations
    ->
re-read the active process graph
    ->
evaluate accumulated evidence
    ->
continue, retrieve more context, or intervene

Core Concepts

Project

A Project is the memory scope associated with one supervised task or execution session.

All processes and their memories belong to the same Project.

Key

A Key represents a coherent execution process.

A process is a strategy or line of work pursued toward a goal or subgoal. It is not a single command, tool call, or reasoning message.

Examples:

process_001_python_bruteforce
process_002_compiled_search
process_003_validate_round_keys

A process should remain active while the agent is pursuing substantially the same strategy.

A new process is created when the execution meaningfully pivots to a different strategy or line of work.

objective is a special Key reserved for task-level requirements and constraints that apply across processes.

Category

Each process uses a small fixed ontology.

process_N
├── strategy
├── evidence
└── summary

The Supervisor must not invent arbitrary categories.

STRATEGY

Describes what the agent is trying to do and how the approach evolves.

Typical content includes:

current plan;

important attempts;

decisions;

pivots;

interventions that change the direction of the process.

EVIDENCE

Describes what execution has demonstrated.

Typical content includes:

discoveries;

errors;

measured behavior;

partial results;

failed assumptions;

validations;

evidence for or against continuing the current strategy.

SUMMARY

A compact description generated when a process is closed or superseded.

The Summary should preserve the decision-relevant outcome of the process without deleting the underlying memories.

Example:

Python brute force was abandoned because measured throughput was
insufficient for the required search space. The implementation was
functionally correct, but repeated optimization produced only marginal
improvement. The next process moved the search loop to compiled code.

Future reviews should prefer the Summary first and expand the underlying graph only when more detail is required.

Process Lifecycle

A process can be:

ACTIVE
SUCCEEDED
FAILED
SUPERSEDED
ABANDONED

Typical lifecycle:

START PROCESS
     |
     v
   ACTIVE
     |
     +--> accumulate STRATEGY
     |
     +--> accumulate EVIDENCE
     |
     +--> create typed relations
     |
     v
Supervisor evaluates accumulated evidence
     |
     +--> continue same process
     |
     +--> intervene but keep process
     |
     +--> close process
              |
              v
          write SUMMARY
              |
              v
       start next process

A process should not be closed only because time has passed.

It should be closed when the accumulated evidence supports that the strategy succeeded, failed, was abandoned, or was superseded.

Mandatory Memory Consumption

The Memory MCP is not an optional tool that the Supervisor may ignore.

Before every meaningful REVIEW, the system must provide the Supervisor with an up-to-date representation of the active process graph.

The Supervisor must therefore always reason from:

recent execution
+
current process graph

and not from recent trajectory alone.

This rule is deterministic and belongs to the orchestration flow, not to the LLM prompt.

Minimum Current Process Context

The normal review context should include a compact graph-derived view such as:

CURRENT PROCESS
process_001_python_bruteforce
status: ACTIVE

CURRENT STRATEGY
- brute-force FEAL round keys in Python
- optimize candidate evaluation loop

EVIDENCE FOR CONTINUING
- implementation appears functionally correct

EVIDENCE AGAINST CONTINUING
- measured throughput remains insufficient
- no round key has been recovered
- latest optimization produced only marginal improvement

UNRESOLVED
- current strategy has not produced a partial key

IMPORTANT RELATIONS
- python_bruteforce FAILED_BECAUSE insufficient_throughput
- optimization TESTED_BY benchmark_result

RECENT MEMORY CHANGES
- new evidence: optimization improved throughput only marginally

This representation is a view derived from the graph. It is not a second private memory maintained by the Supervisor.

Context Depth

GRAMS should avoid loading the entire memory graph into every prompt.

Memory is consumed progressively.

Level 0 — Current Process Index

Used during normal reviews.

Contains a compact view of:

active process;

strategy titles;

evidence titles;

important relations;

current process status;

recent memory changes;

process summary when available.

The goal is to keep normal memory cost small.

Level 1 — Expanded Current Process

Triggered when the current process requires deeper evaluation.

Examples:

POSSIBLE_PROGRESS_STALL
POSSIBLE_RESEARCH_LOOP
POSSIBLE_HYPOTHESIS_OSCILLATION

The system expands the relevant Strategy and Evidence memories before the Supervisor decides whether to intervene.

Level 2 — Related Process Neighborhood

Used when the active process alone is insufficient.

The Supervisor may inspect related previous processes and memories through relations such as:

SUPERSEDES
FAILED_BECAUSE
DEPENDS_ON
CONTRADICTS
SUPPORTS
TESTED_BY
PRODUCED
VALIDATES

This allows the Supervisor to reconstruct decision-relevant history without loading the entire execution trajectory.

Progress Signals

GRAMS distinguishes activity from meaningful progress.

Examples:

installing a package
    -> activity

discovering that a hypothesis is false
    -> knowledge gain

producing a new candidate artifact
    -> progress

successfully validating a candidate
    -> strong progress

Operational signals may identify situations that deserve deeper memory review:

POSSIBLE_PROGRESS_STALL
POSSIBLE_RESEARCH_LOOP
POSSIBLE_HYPOTHESIS_OSCILLATION
DELIVERABLE_MISSING
VALIDATION_MISSING

These signals are not decisions.

They do not automatically cause an intervention.

They only force the Supervisor to inspect more graph context before deciding.

Process Persistence Review

A process that remains active for a significant amount of time without meaningful progress should trigger:

POSSIBLE_PROGRESS_STALL

The response is:

same process persists
        |
        v
POSSIBLE_PROGRESS_STALL
        |
        v
mandatory deeper MCP review
        |
        v
expanded Strategy + Evidence
        |
        v
Supervisor evaluates:
"is continuing this strategy still justified?"
        |
        +--> YES -> SILENT / CONTINUE
        |
        +--> UNCERTAIN -> EXPAND RELATED GRAPH
        |
        +--> NO -> INTERVENE / CLOSE PROCESS

Elapsed time never directly justifies an intervention.

Time only forces a new evaluation of the strategy.

The decision must be based on accumulated evidence from the graph.

GRAMS must not use Harbor's remaining timeout, benchmark deadline, or percentage of execution budget to decide whether to intervene.

Memory-Conditioned Intervention

An intervention should be grounded in stored execution knowledge whenever that knowledge exists.

Bad:

You seem stuck. Reconsider your approach.

Preferred:

The current Python search has not produced a key, and the measured
throughput stored in the current process remains insufficient despite
the latest optimization. Continuing the same strategy is not currently
supported by the accumulated evidence. Reconsider the implementation
approach before further refinement.

The Supervisor should explain:

what behavior is failing
+
what stored evidence supports that conclusion
+
what kind of next action should be reconsidered

The Supervisor should not become a second Action Agent by directly solving the task.

Graph Semantics

The graph should preserve decision-relevant relationships between memories and processes.

Examples:

strategy_A FAILED_BECAUSE evidence_X

strategy_B SUPERSEDES strategy_A

strategy_B PRODUCED result_Y

result_Y VALIDATES strategy_B

attempt_C TESTED_BY validation_Z

process_002 SUPERSEDES process_001

result_R DEPENDS_ON discovery_D

The graph is not only storage.

Relations should affect retrieval and the context presented to the Supervisor.

A retrieved memory may lead to its causal or decision-relevant neighbors even when those neighbors are not semantically similar to the current event.

Review Invariants

The orchestration layer should enforce the following functional rules:

A meaningful REVIEW must never run without a valid view of the active process graph.

A progress-stall review must inspect the current process through the MCP before intervention.

A hypothesis or strategy revisit should inspect relevant prior evidence before intervention.

Closing a process requires generating or updating its Summary.

Starting a new process should preserve the relationship with the previous process when one exists.

Memory failures should not crash the entire Supervisor runtime; they must be observable and recoverable.

Recent trajectory must not replace persistent process memory.

The Action Agent remains unchanged and does not directly manage GRAMS memory.

Target Supervisor Flow

START
  |
  v
READ_INBOX
  |
  v
ENSURE_ACTIVE_PROCESS
  |
  v
UPDATE_PROCESS_MEMORY
  |
  v
LOAD_CURRENT_PROCESS_CONTEXT
  |
  v
DETECT_OPERATIONAL_SIGNALS
  |
  v
REVIEW
  |
  +--> CONTINUE / SILENT
  |
  +--> NEED_MORE_MEMORY
  |       |
  |       v
  |   EXPAND_GRAPH
  |       |
  |       v
  |     REVIEW
  |
  +--> WRITE_MEMORY
  |
  +--> INTERVENE
  |
  +--> CLOSE_PROCESS
          |
          v
      WRITE_SUMMARY
          |
          v
      START_PROCESS

The exact LangGraph node boundaries are an implementation detail.

The functional requirement is that memory consumption and process lifecycle are enforced by orchestration rather than being optional choices left entirely to the review model.

Components

grams-opencode/opencode_plugin/: sends normalized OpenCode execution events.

grams-app/supervisor/api/: HTTP event ingress.

grams-app/supervisor/inbox/: durable event journal and lease operations.

grams-app/supervisor/runtime/: Supervisor lifecycle.

grams-app/supervisor/agent/: LangGraph state, routing, prompts, and review nodes.

grams-app/supervisor/memory/: Python Memory MCP integration.

grams-app/supervisor/opencode/: OpenCode control/context integration.

grams-app/supervisor/platform/sqlite/: Supervisor SQLite setup.

grams-app/memory-mcp/: standalone Go graph-memory MCP.

Current Implementation Status

The durable event transport and SQLite Inbox are already part of the system.

The standalone Go Memory MCP is available as a separate component.

The architecture described above is the target Supervisor design: process-managed graph memory, mandatory current-process context, progressive graph expansion, and memory-conditioned intervention.

Implementation details may evolve, but the functional invariants in this document should remain stable.

Local Setup

Install Python dependencies:

python -m pip install -r requirements.txt

Start the Supervisor API:

.venv/bin/uvicorn supervisor.app:app \
  --app-dir grams-app/supervisor \
  --host 0.0.0.0 \
  --port 8765

The Supervisor database path defaults to:

~/Library/Application Support/grams/supervisor.db

Override it with:

GRAMS_SUPERVISOR_DB_PATH

or:

GRAMS_DB_PATH

Start the Memory MCP:

cd grams-app/memory-mcp
go run ./cmd/server

Event Contract

The event receiver exposes:

POST /events

Valid JSON is normalized and persisted before the receiver returns 202.

Invalid JSON returns 400.

If the Inbox or SQLite database is unavailable, the receiver returns 503.

The OpenCode plugin endpoint is configured with:

GRAMS_EVENT_ENDPOINT=http://127.0.0.1:8765/events

Verification

pytest -q grams-app/tests/test_event_server.py grams-app/tests/test_observability.py

git diff --check

Research Direction

GRAMS is not intended to be a benchmark-specific planner or a second task-solving agent.

The research question is whether a dedicated Supervisor can maintain and use relational execution memory to preserve decision-relevant state across long-horizon tasks.

The core hypothesis is:

As execution history grows, supervisor-managed relational memory can preserve why strategies succeeded, failed, or were superseded, allowing the Supervisor to make better continuation and intervention decisions without repeatedly loading the full trajectory.

The intended comparison is not simply:

memory vs no memory

but eventually:

baseline action agent

vs

supervisor without persistent memory

vs

supervisor with flat process memory

vs

GRAMS:
supervisor-managed process graph
+ typed relations
+ progressive graph retrieval
+ memory-conditioned intervention

The graph should matter because it preserves decision structure, not merely because it stores more text.
