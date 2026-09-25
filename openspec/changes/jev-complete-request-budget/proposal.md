# Jev complete request budget and evidence pruning

## Objective

Ensure every Jev API request fits the configured serialized request-byte budget, including question definitions/criteria and state. When oversized, the client SHALL deterministically prune lower-priority context—oldest evidence first—while preserving newest/currently relevant evidence and valid in-scope selection; it SHALL never send an oversized body or make identical, pointless retries.

## Relevant Context

- `JevClient.system_one` in `grams-app/supervisor/agent/services/jev_service.py` prepares and sends System One requests containing `model`, `state`, and `questions`.
- The client currently uses `compact_jev_state` and recursively splits question batches when a budget error indicates questions may be split. Existing request/retry code measures serialized UTF-8 bytes.
- A cancelled, verified path-tracing trial exposed repeated local `_JevPayloadBudgetError` failures for `evidence_memory_1` after the evidence list grew.
- The existing proposal `jev-progressive-context-retries` covers request sizing and question splitting; this change specifically requires the complete body to be budgeted together and the evidence/options pruning and scope behavior described below.

## Scope

Include request construction and deterministic sizing/pruning for Jev calls, explicit failure for an irreducible minimum request, and regression tests for large evidence options, question size, ordering, and selection scope.

Exclude changes to Jev's response contract, business decision policy, durable Inbox data, or unrelated HTTP retry behavior.

## Expected Behavior

Before each POST, the exact serialized bytes to be transmitted are measured, with the configured maximum applying to the entire body (`model`, `state`, and `questions`, including criteria). If necessary, evidence/context is pruned deterministically, removing older/lower-priority material before newer/currently relevant material. Any retained or selected evidence reference remains valid and within the applicable process/project scope. If no valid minimum request fits, the client fails locally and does not POST.

## Functional Requirements

1. The client SHALL measure the exact final serialized UTF-8 request body, including all question definitions and criteria as well as state and model, before every POST.
2. Every transmitted body SHALL be no larger than the configured budget. The HTTP transport SHALL receive the same bytes that were measured.
3. When an over-budget request can be reduced, pruning SHALL be deterministic for identical inputs and configuration. It SHALL preferentially remove oldest evidence/context first and retain newest/currently relevant evidence for the current decision as long as possible.
4. Fixed question names, instructions, criteria and required JSON structure SHALL NOT be silently truncated or malformed for request sizing. Dynamic evidence choices retain the existing bounded per-option description (500 characters); when the aggregate exceeds the limit, whole older options MAY be omitted before constructing the question, keeping at least one in-scope evidence choice and `NONE`. Question splitting may be used only if each resulting request independently passes the full-body byte check.
5. Evidence candidates/options and any references used for selection SHALL remain valid and in scope after pruning; pruning SHALL NOT make an out-of-process or out-of-project item selectable.
6. The client SHALL detect when another compaction/retry would produce an identical payload or no further valid reduction. It SHALL stop without sending duplicate, pointless retries.
7. If the minimum valid request cannot fit, the client SHALL raise an explicit locally classifiable budget error before HTTP. It SHALL never send an oversized request as a fallback.
8. Pruning SHALL operate on a request view and SHALL NOT mutate caller state or durable evidence.

## Non-Functional Requirements

- Sizing and pruning SHALL be deterministic and bounded; no extra Jev request may be used to discover whether an oversized body fits.
- Diagnostics SHALL distinguish local budget exhaustion from remote rejection without logging full request content or credentials.

## Affected Components

- `grams-app/supervisor/agent/services/jev_service.py`: full-body guard, compact/retry termination, and safe question batching.
- `grams-app/supervisor/agent/state_builder.py`: evidence ordering/prioritization or compact request-view behavior, as needed.
- Jev service and state-builder tests under `grams-app/tests/`.

## Constraints

- Preserve existing Jev question/answer validation and current process/project scope checks.
- Preserve durable input and existing non-size HTTP error handling.
- No HTTP request may be made with a body exceeding the configured budget.

## Edge Cases

- Many evidence options cause an otherwise valid `evidence_memory_1` question to exceed the limit.
- A question's criteria/options alone exceed the remaining budget, independently of state size.
- Evidence is already ordered newest-first, oldest-first, or has equal/missing timestamps; pruning must still be stable and newest/current relevance must be preserved where identifiable.
- A selected reference is pruned, unknown, or outside the active process/project scope.
- The minimum valid request exceeds the budget, including a single irreducible question.
- A compaction step produces the same serialized body as the preceding attempt.

## Error Handling

An irreducible over-budget body SHALL fail locally with a classified error and zero HTTP calls for that body. Exhausted pruning SHALL not loop or retry an identical payload. Existing handling for remote errors unrelated to size SHALL remain unchanged.

## Acceptance Criteria

1. Captured transport bodies for all tested Jev calls are byte-for-byte the measured bodies and never exceed the configured limit.
2. Large evidence lists are reduced deterministically with oldest evidence removed preferentially; newest/currently relevant items survive whenever a valid fitting request permits.
3. Oversized question criteria/options are accounted for independently of state; older dynamic evidence options are removed before calling Jev, and irreducible fixed questions fail locally.
4. Pruned evidence selections remain valid and within process/project scope; invalid or out-of-scope selection is never emitted.
5. An irreducible minimum request and a no-progress compaction both terminate before another oversized or identical POST.
6. The caller's original state/evidence remains unchanged.

## Test Scenarios

- Fake HTTP transport captures exact bytes for many evidence options; assert budget compliance, oldest-first deterministic pruning, and preservation of newest/current relevance.
- Make a single question's criteria large while state is small, and separately make many questions collectively large; verify complete-question accounting and valid split behavior or explicit pre-HTTP failure.
- Exercise evidence in differing input orders and ties/missing ordering metadata; verify the specified stable priority and repeatability.
- Test selected evidence references after pruning, including valid in-scope selection and out-of-process/out-of-project references.
- Configure a limit below the irreducible request size; assert classified local error and zero POSTs.
- Force compaction to yield no change; assert termination without an identical repeat request.
- Assert request preparation does not mutate source state.

## Out of Scope

- Changing Jev's API limit, response schema, or decision policy.
- Persisting pruned request state or changing Inbox records.
- Retrying unrelated HTTP/network errors.

## Assumptions

- The configured limit represents the maximum serialized request bytes as used by the current client convention.
- “Current relevance” is inferable from existing evidence ordering/metadata and references in the current questions; no new semantic ranking model is required. If the implementation cannot establish a relevant item under existing data, it must use a stable ordering and retain valid scope rather than invent relevance.
