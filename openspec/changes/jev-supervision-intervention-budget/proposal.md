# JEV intervention authorization and evidence selection

## Objective

Ensure an explicit JEV `INTERVENE` decision routes to intervention when its evidence and reason codes are valid and in scope, independent of confidence/context thresholds; prevent repeated memory IDs across independent JEV evidence slots from failing a cycle, while retaining validation and at-most-once persistence.

## Relevant Context

- The supervision graph routes `INTERVENE` to `BUILD_INTERVENTION` and `NEED_MORE_MEMORY` to graph expansion.
- The existing supervision decision behavior applies confidence/context and threshold-based fallback logic. This change narrows threshold use to fallback after `NEED_MORE_MEMORY` has exhausted three graph expansions.
- Evidence selection and intervention persistence are part of the supervision/intervention path; existing Inbox durability and delivery contracts remain authoritative.

## Scope

Correct decision routing, evidence ID validation/deduplication, and intervention persistence idempotency, with regression tests. Does not change the JEV response schema or graph expansion bound.

## Expected Behavior / Functional Requirements

1. An explicit `INTERVENE` choice SHALL route to intervention without applying confidence, context-sufficiency, or threshold gates, provided required evidence IDs are valid and in scope and required reason codes are valid.
2. Thresholds SHALL be consulted only as decision fallback after `NEED_MORE_MEMORY` has exhausted exactly three graph expansions. They SHALL NOT override or block an explicit valid `INTERVENE` choice.
   A caller configured with a lower expansion limit SHALL terminate without the three-expansion intervention fallback.
3. Repeated valid memory IDs supplied by independent JEV evidence slots SHALL be safely deduplicated and SHALL NOT fail the supervision cycle. Deduplication SHALL preserve the valid selected evidence for the intervention.
4. Invalid or out-of-scope evidence IDs SHALL remain rejected, including when a response combines such an ID with repeated valid IDs. Invalid reasons or missing required evidence SHALL not produce an intervention.
5. Intervention persistence SHALL occur at most once for a supervised Inbox cycle, including when the same evidence is selected repeatedly or processing is retried. The send stage SHALL own durable audit creation/reconciliation; the record stage SHALL NOT perform a second independent write. Existing durable delivery/idempotency semantics SHALL be preserved.
6. Tests SHALL verify routing and persistence, not only the proposed decision value.

## Non-Functional Requirements

- Validation and deduplication SHALL be deterministic for identical inputs.
- Rejection and deduplication SHALL not weaken scope validation or durable event guarantees.

## Affected Components

- JEV supervision decision and graph routing.
- Evidence selection/validation and intervention build/persist path.
- Corresponding supervision, evidence-selection, and intervention persistence tests.

## Constraints

- Preserve the existing JEV response schema, three-expansion limit, SQLite Inbox semantics, and intervention delivery contract.
- Modify only the relevant behavior; do not introduce a separate queue or persistence mechanism.

## Edge Cases and Error Handling

- Multiple independent slots select the same valid in-scope memory ID: deduplicate, continue successfully.
- A repeated valid ID is accompanied by an invalid or out-of-scope ID: reject the invalid selection; do not silently filter it into an authorized intervention.
- Explicit `INTERVENE` with low confidence or low context diagnostic: proceed only if evidence and reasons pass validation.
- `NEED_MORE_MEMORY` before expansion exhaustion: continue expansion; at the third exhausted expansion, apply existing fallback thresholds.
- Retry or duplicate processing: do not persist the same intervention more than once.
- A persisted intervention audit SHALL not be interpreted as a process-close summary on replay.

## Acceptance Criteria / Test Scenarios

- An explicit valid `INTERVENE` reaches intervention despite low confidence/context and regardless of threshold values.
- Invalid, out-of-scope, missing evidence, or invalid reason selections do not reach persistence/delivery.
- `NEED_MORE_MEMORY` follows expansion routing until three expansions are exhausted; thresholds are only applied in the exhausted fallback.
- Duplicate valid IDs across distinct evidence slots do not fail and result in one deduplicated evidence selection.
- Mixed duplicate-valid and invalid/out-of-scope IDs are rejected.
- Repeated processing/retry of an authorized decision persists the intervention no more than once.
- A crash after audit creation but before the send node result is recorded recovers that same durable audit by cycle key; the record node never creates a competing audit.
- Existing tests for unrelated decision routes and intervention idempotency continue to pass.

## Out of Scope

- Changing JEV's response format, evidence scope definitions, reason-code vocabulary, Inbox retry policy, or the number of graph expansions.
- Existing close-process outcome validation is independent of the intervention fallback and remains unchanged.
- Trial execution; the trial was canceled before implementation.

## Assumptions

- “Independent JEV slots” refers to distinct evidence selection fields/slots in one JEV response, not separate events.
- The current evidence scope and reason validity rules define what counts as valid; this change preserves those rules.
