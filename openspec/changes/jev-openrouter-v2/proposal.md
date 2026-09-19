# GRAMS v2 Jev/OpenRouter Supervisor

## Objective

Rebuild the Supervisor around a strict model boundary: Jev makes typed
probabilistic decisions, OpenRouter generates text or JSON, and Python,
LangGraph, Inbox and Memory MCP control ordering and side effects.

## Required Flow

`READ_INBOX -> ENSURE_ACTIVE_PROCESS -> LOAD_PROCESS_CONTEXT ->
ASSESS_PROCESS_CONTINUITY [Jev]`. `SAME_PROCESS` runs OpenRouter memory
extraction, deterministic MCP application, a second context load, and two-call
Jev supervision. `NEED_MORE_MEMORY` expands deterministically and returns to
supervision. `INTERVENE` generates a message with OpenRouter, sends it through
OpenCode, records evidence in MCP, and finalizes. `CLOSE_PROCESS` generates a
summary, persists it, closes the process, and finalizes. `NEW_PROCESS` closes,
creates a successor, reloads context, and resumes extraction.

## Model Contracts

Jev receives structured state and questions. Continuity is one `Choice` with
`SAME_PROCESS` and `NEW_PROCESS`; it does not generate names or reasons.
Supervision has two calls: three independent `Noul` diagnostics for progress
stall, strategy support, and context sufficiency; then a `Choice` action call
with diagnostics added to the state. Preserve probabilities and confidence.

OpenRouter is limited to `EXTRACT_MEMORY_UPDATE`, `WRITE_PROCESS_SUMMARY`, and
`BUILD_INTERVENTION`. Nodes provide the exact prompts and payloads. The client
services remain transport-only and never contain routing policy or prompts.

## State and Builders

Add canonical `build_jev_process_state` output containing task, current process,
strategy, evidence, relations, recent final execution events, operational
metrics, and optional expanded memory. Do not include timeout/deadline data or
precomputed semantic diagnoses. `LOAD_PROCESS_CONTEXT` must return compact
process, strategy, evidence, relations, summary and recent changes.

## Deterministic Boundaries

Only the allowlisted relations are accepted. MCP writes, graph expansion,
process lifecycle, OpenCode delivery, Inbox ACK/fail, validation and confidence
thresholds remain deterministic. No model receives tools or performs side
effects. OpenRouter memory refs use local `new_N` identifiers resolved after
creation.

## Configuration and Observability

Use `TYPESAFE_API_KEY`, `TYPESAFE_MODEL`, `OPENROUTER_API_KEY`,
`OPENROUTER_BASE_URL`, `OPENROUTER_DEPLOYMENT`, and future explicit Jev
threshold variables. Record model/version, latency, probabilities, confidence,
process ID, action, expansion depth and usage without credentials.

## Acceptance Criteria

- No node builds Jev state from ad hoc prompt strings.
- Jev decisions preserve full distributions and confidence.
- OpenRouter only generates memory text, summaries and intervention text.
- Every routing decision follows a current process-context load.
- No timeout or benchmark budget is sent to Jev.
- Unit tests cover the two Jev calls, OpenRouter payloads, context builders,
  process transitions, expansion limits, intervention delivery and exact Inbox
  leases.
