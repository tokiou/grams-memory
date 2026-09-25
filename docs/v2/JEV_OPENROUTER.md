# GRAMS v2: Jev and OpenRouter

## Objective

GRAMS keeps one logical Supervisor. OpenCode solves the task, Inbox transports
events, Memory MCP is the durable execution-memory source of truth, and
LangGraph controls ordering and side effects.

The model boundary is strict:

- **Jev** makes bounded, probabilistic, typed decisions.
- **OpenRouter** generates text or structured JSON only where text is required.
- **Python and MCP** enforce validation, routing, persistence, leases, ACKs, and
  process lifecycle.

The Supervisor must consume an updated view of the current process memory before
deciding continuity, intervention, or closure.

## Target Flow

```text
START -> READ_INBOX -> ENSURE_ACTIVE_PROCESS -> LOAD_PROCESS_CONTEXT
  -> ASSESS_PROCESS_CONTINUITY [JEV]
      SAME_PROCESS -> EXTRACT_MEMORY_CANDIDATES [OPENROUTER]
                    -> CURATE_MEMORY_CANDIDATES [JEV]
                    -> MATERIALIZE_MEMORIES [OPENROUTER]
                    -> APPLY_MEMORY_UPDATE [PYTHON + MCP]
                    -> LOAD_PROCESS_CONTEXT [PYTHON + MCP]
                    -> SUPERVISION_DECISION [JEV]
      NEW_PROCESS  -> WRITE_PROCESS_SUMMARY [OPENROUTER]
                    -> CLOSE_CURRENT_PROCESS [PYTHON + MCP]
                    -> START_NEW_PROCESS [PYTHON + MCP]
                    -> LOAD_PROCESS_CONTEXT
                    -> EXTRACT_MEMORY_CANDIDATES
```

`SUPERVISION_DECISION` routes to `CONTINUE`, `NEED_MORE_MEMORY`, `INTERVENE`,
or `CLOSE_PROCESS`. `NEED_MORE_MEMORY` runs deterministic `EXPAND_GRAPH` and
returns to `SUPERVISION_DECISION`. `INTERVENE` runs `BUILD_INTERVENTION`
through OpenRouter, then deterministic delivery and audit nodes.

The old `DETECT_PROGRESS_STALL` and generative `REVIEW` split is removed.
Progress and strategy support are diagnostics inside `SUPERVISION_DECISION`.

## Jev Contract

Jev receives structured state and independent questions. Questions in one call
share input state but must not depend on each other's answers.

`ASSESS_PROCESS_CONTINUITY` uses one `Choice` with exactly:

- `SAME_PROCESS`
- `NEW_PROCESS`

It must not generate names or free-form reasons. A new process starts with a
deterministic name such as `process_002`; semantic descriptions arrive through
later STRATEGY memories.

`SUPERVISION_DECISION` uses two Jev calls:

1. **Diagnostics call**, with three independent `Noul` questions:
   - `progress_stall_probability`;
   - `strategy_supported_probability`;
   - `context_sufficient_probability`.
2. **Action call**, after adding those diagnostics to the state, with a `Choice`
   over `CONTINUE`, `NEED_MORE_MEMORY`, `INTERVENE`, and `CLOSE_PROCESS`.

The complete probability distributions and confidence values are retained in
the transient state and observability records. Elapsed time, benchmark timeout,
and remaining Harbor budget are never sent to Jev.

## Canonical Jev State

The state is built deterministically by `build_jev_process_state`, rather than
assembled in prompt strings:

```json
{
  "task": {"objective": "...", "global_constraints": []},
  "current_process": {
    "id": "process_001",
    "name": "python_bruteforce",
    "status": "ACTIVE",
    "started_at": "...",
    "age_seconds": 620
  },
  "strategy": [],
  "evidence": [],
  "relations": [],
  "recent_execution": [],
  "operational_metrics": {
    "process_age_seconds": 620,
    "seconds_since_last_strategy_change": 420,
    "seconds_since_last_evidence": 390,
    "events_since_last_evidence": 18,
    "recent_event_count": 20,
    "recent_tool_call_count": 9,
    "recent_file_change_count": 0,
    "recent_validation_count": 0,
    "recent_error_count": 2
  },
  "expanded_memory": null
}
```

No precomputed diagnosis, next action, timeout, deadline, or benchmark budget
is included. Recent execution uses final meaningful events only:
`REASONING_FINAL`, `TEXT_FINAL`, `TOOL_CALL_FINAL`, `TOOL_RESULT_FINAL`,
`FILE_CHANGE_FINAL`, `MESSAGE_COMPLETED`, and `MESSAGE_ERROR`.

## Generative Boundary

OpenRouter/DeepSeek is used only for:

- extracting high-value STRATEGY/EVIDENCE memory text;
- writing a compact process summary;
- writing the dynamic intervention message after Jev has already selected
  `INTERVENE`.

OpenRouter never chooses routing, calls tools, writes MCP, ACKs Inbox events, or
decides whether an intervention is needed. Its outputs are validated by Python.

Memory extraction may propose only the fixed categories STRATEGY and EVIDENCE
and the explicit relation allowlist:

```text
SUPPORTS, CONTRADICTS, TESTED_BY, PRODUCED, SUCCEEDED_WITH,
FAILED_BECAUSE, BLOCKED_BY, DEPENDS_ON, SUPERSEDES, VALIDATES
```

New memory references use local `new_N` refs and are resolved by deterministic
application code after MCP creates the memories.

## Process Context and Retrieval

`LOAD_PROCESS_CONTEXT` returns a compact, consistent view containing the active
process, STRATEGY, EVIDENCE, relations, optional SUMMARY, and recent changes.
`EXPAND_GRAPH` adds only requested memories, relations, neighbors, summaries,
or related processes to `expanded_memory_context`; it never replaces the normal
process context. `memory_expansion_depth` prevents loops.

## Configuration

The services read configuration from the environment:

```text
TYPESAFE_API_KEY
TYPESAFE_MODEL=jev-latest
OPENROUTER_API_KEY
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_DEPLOYMENT=<chosen-generative-model>
```

Thresholds are explicit configuration, not prompt rules:

```text
JEV_NEW_PROCESS_MIN_PROB
JEV_ACTION_MIN_CONFIDENCE (only after three NEED_MORE_MEMORY expansions)
JEV_EXHAUSTED_INTERVENTION_MIN_PROB (only after three NEED_MORE_MEMORY expansions)
JEV_OUTCOME_MIN_CONFIDENCE (process closure only)
```

An explicit Jev `INTERVENE` is routed without confidence or context-sufficiency
thresholds; the selected evidence and reason codes must still be valid. Repeated
evidence IDs in different selection slots are deduplicated before delivery.

## Observability and Evaluation

Record model/version, latency, probabilities, confidence, process ID, action,
expansion depth, and usage metadata without API keys or unnecessary payloads.
Replay datasets should cover same-process refinement, material pivots, early/mid/
late FEAL execution, and Raman activity without convergence. Evaluate Jev's
calibration and false pivots, interventions, and closures before Harbor trials.
