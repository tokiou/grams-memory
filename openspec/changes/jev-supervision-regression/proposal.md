# JEV supervision and failure-path regression correction

## Objective

Correct the observed JEV supervision regression so an explicit, confident, evidence-backed intervention can occur after retrieval even when the context diagnostic remains low; oversized JEV requests and regenerated memory proposals should not repeatedly fail an otherwise valid cycle.

## Relevant Context

- `supervision_decision.py` obtains diagnostics and action probabilities repeatedly across expansion depths. It overrides the selected action to `NEED_MORE_MEMORY` when context sufficiency is below threshold, but after expansion exhaustion promotes that action to `INTERVENE` when the INTERVENE probability exceeds a separate threshold. It then requires evidence and reason selections.
- The canceled path-tracing log shows repeated assessments for one batch, often with `action=NEED_MORE_MEMORY`, `INTERVENE` probability near 0.8, and later `expansion_depth=1/2`; no evidence here establishes that probability alone should authorize an intervention.
- The same run ended with a 32,768-byte request-budget failure after question splitting, scheduled the six events back to PENDING, and failed the cycle. The log also records an incompatible curation-metadata RuntimeError earlier. Consult the run DB and logs when implementing to classify the full node/agent failures and distinguish root causes from symptoms.
- The existing `jev-progressive-context-retries` change addresses request byte sizing/retries and question splitting. This change must complement it, not duplicate or overwrite it.

## Scope

Specify correction of intervention gating and supervision routing, and recovery/containment of request-budget and related node/agent failures evidenced in the canceled run. Include regression coverage using the recorded failure patterns. Do not change durable Inbox semantics or redefine Jev's external response schema.

## Functional Requirements

1. Low context sufficiency SHALL trigger retrieval before intervention. After at least one expansion, an explicit `INTERVENE` choice with configured confidence, configured intervention probability, and in-scope evidence SHALL be allowed even if the diagnostic remains low. Probability alone SHALL NOT promote a `NEED_MORE_MEMORY` choice to `INTERVENE`; exhaustion without independent authorization SHALL resolve to `CONTINUE`.
2. An intervention SHALL be emitted only when its authorization conditions are satisfied and the required in-scope evidence and reason codes are valid. Low-confidence/ambiguous decisions SHALL NOT cause intervention delivery as a side effect of graph-expansion exhaustion.
3. Expansion SHALL be bounded and each expansion depth SHALL be evaluated at most once per supervision cycle; cycles SHALL not repeat identical Jev assessments or loop on unchanged context. The final route SHALL be deterministic for identical inputs/configuration.
4. JEV requests SHALL respect the configured serialized-byte budget on every HTTP attempt, including split question batches. State compaction SHALL handle oversized optional candidate and prose fields while leaving question definitions intact and marking omitted context; an indivisible request still failing locally SHALL follow the existing bounded Inbox retry policy.
5. Replaying a batch SHALL reuse already persisted memories by stable candidate reference. The same event MAY support multiple distinct facts; later curation metadata for an already persisted fact SHALL NOT overwrite the first durable description or cause an otherwise valid event to fail.
6. Logs/telemetry SHALL identify proposed and effective decision, expansion depth and JEV request-budget exhaustion without logging full prompts, payloads, or credentials.

## Acceptance Criteria

- Given insufficient context and high INTERVENE probability, expansion exhaustion does not deliver an intervention absent an explicit confident choice and valid evidence; an explicit evidence-backed intervention after retrieval is possible.
- Tests cover low confidence, threshold boundaries, insufficient context, exhausted and non-exhausted expansion, missing/invalid evidence, and verify the actual delivery node is not reached when intervention is denied.
- A single batch cannot cause repeated identical supervision calls beyond the configured expansion bound; routing terminates deterministically.
- Captured Jev HTTP bodies never exceed the configured byte maximum, including recursively split question batches; long objective and candidate prose compacts with an explicit omission marker.
- A replay with changed candidate phrasing or metadata reuses the durable memory; two distinct facts from the same source event are not incorrectly conflated.
- Existing progressive-context retry behavior and unrelated valid interventions remain covered and unchanged.

## Affected Components

- `grams-app/supervisor/agent/nodes/supervision_decision.py`, supervision/expansion graph routing, intervention build/send/record nodes.
- `grams-app/supervisor/agent/services/jev_service.py` and related request-budget handling, in coordination with `jev-progressive-context-retries`.
- Existing bounded Inbox retry policy remains unchanged; associated tests and observability.

## Constraints and Out of Scope

- Preserve SQLite Inbox as durable source of events and existing intervention idempotency/delivery contracts.
- Do not change Jev response schema, unrelated memory semantics, or introduce a new supervision architecture.
- Do not treat log probabilities as calibrated truths beyond the existing configured thresholds.

## Assumptions / Open Questions

- The run log identifies observed symptoms, not a complete causal diagnosis; implementation SHALL inspect the referenced DB and surrounding logs before attributing each node/agent failure.
- Chosen policy: `CONTINUE` after exhausted expansion without independently justified intervention; the existing action-confidence and exhausted-intervention probability thresholds are both required after retrieval when context remains low.
