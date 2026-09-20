# Prevent self-edges and partial writes in `APPLY_MEMORY_UPDATE`

## Objective

Make `apply_memory_update` deterministic and retry-safe when distinct proposed
candidate references resolve to the same existing memory or to the same planned
new memory. The node SHALL detect the resulting self-relation before calling
Memory MCP, reject the invalid proposal without writes, and preserve the
existing durable-cycle reconciliation behavior so that a retry cannot create
duplicate tagged memories or turn a prior partial write into an avoidable
proposal conflict.

## Relevant Context

- `grams-app/supervisor/agent/nodes/apply_memory_update.py` currently validates
  the proposal, checks relation scope, resolves existing memories, creates new
  memories while iterating candidates, and only then resolves and links
  relations.
- Its identity key is `(category, title.casefold(), content.casefold())` after
  trimming. The candidate loop updates `existing_by_identity` after a create,
  so two distinct candidate references can resolve to one ID.
- `validate_memory_proposal` rejects a relation whose literal `source_id` and
  `target_id` are equal, but it cannot detect equality introduced by candidate
  deduplication. It also rejects duplicate relation candidates.
- The Go Memory MCP intentionally rejects `source_id == target_id` with
  `invalid argument: self edge`. The Python client surfaces that failure from
  `memory.link`; it does not provide a batch transaction or delete/rollback
  operation for a group of memory writes.
- Durable retries use `cycle_key(state)` and store tags of the form
  `cycle-id:candidate_ref` in the created memory description. After a context
  reload, an exact tagged candidate is reused; a changed candidate with the
  same reference currently raises `regenerated memory proposal conflicts with
  the durable cycle proposal`.
- `SupervisorRuntime` fails the claimed Inbox batch when graph execution raises
  and does not ACK it. Existing tests cover ordinary candidate deduplication,
  out-of-scope relation rejection, tagged-memory reuse, and tagged-memory
  conflict, but not deduplication-induced self-edges or write atomicity around
  those cases.

## Scope

Included:

- A non-mutating preflight phase for candidate identity resolution, cycle-tag
  reconciliation, relation endpoint resolution, and post-resolution self-edge
  detection.
- No-write behavior for deterministic preflight failures.
- Deduplicated successful writes and retry behavior for exact durable cycle
  proposals, including proposals left partially persisted by an unrelated
  operational failure.
- Regression tests for existing-ID collisions, planned-new-ID collisions,
  tagged retry reuse, tagged retry conflicts, and Inbox failure/ACK behavior.

Not included:

- Changing the allowed relation types or the candidate schema.
- Silently dropping, rewriting, or weakening a proposed self-relation.
- A new general-purpose Memory MCP transaction protocol, database API, or
  deletion endpoint.
- Changes to process lifecycle, extraction prompts, LangGraph topology, or
  Inbox lease/backoff policy beyond preserving their current failure semantics.

## Expected Behavior

1. After schema and scope validation, the node computes a complete planned
   mapping from every `candidate_ref` to either an existing memory ID or a
   canonical planned-new-memory group. Candidate references with the same
   identity resolve to the same group, and category remains part of identity.
2. The node resolves every proposed relation against that planned mapping and
   existing scoped IDs before making any `memory.create` or `memory.link` call.
3. If a resolved relation has identical source and target IDs, the whole
   proposal is rejected as an invalid post-deduplication self-relation. The
   node does not call `memory.link`, does not silently skip the relation, and
   does not persist any other candidate in that proposal.
4. If preflight succeeds, each new canonical group is created at most once;
   all candidate references in that group resolve to the returned ID. Existing
   relation identities continue to be skipped, and only non-self, non-existing
   relations are sent to Memory MCP.
5. A retry with an exact cycle-tagged proposal reuses the tagged memories and
   does not create them again. A retry with a changed identity for an already
   tagged `candidate_ref` fails closed with the existing durable-proposal
   conflict and performs no writes.
6. The self-edge case is therefore atomic at the validation boundary: an
   invalid proposal discovered by preflight leaves no newly created memory and
   no new relation. Full atomicity across independent MCP calls is not claimed
   for an unexpected transport or server failure after writes have started.
7. Any unexpected MCP failure still propagates through the existing Supervisor
   runtime failure path. The claimed Inbox events are not ACKed; they remain
   eligible for the existing retry/terminal-attempt policy.

## Functional Requirements

### Preflight and resolution

1. `apply_memory_update` SHALL finish all deterministic checks that can be
   performed from state and the proposal before its first mutating MCP call.
   This includes proposal validation, process-category validation, relation
   scope validation, cycle-tag conflict validation, candidate identity
   resolution, relation endpoint resolution, and post-resolution self-edge
   detection.
2. Preflight SHALL use the same normalized identity semantics as the current
   `_identity` helper: category is significant, while title and content are
   trimmed and case-folded.
3. Preflight SHALL distinguish these endpoint forms and resolve them
   deterministically:
   - a candidate reference resolves to its existing or planned canonical
     memory;
   - an existing memory ID resolves only if it is in the current process scope;
   - an unknown endpoint is rejected before writes.
4. Preflight SHALL inspect cycle-tagged memories before creating any candidate.
   An exact `(candidate_ref, identity)` match SHALL be reusable. A matching
   `candidate_ref` with a different identity SHALL raise an explicit durable
   proposal conflict before any write, including writes for earlier candidates
   in the list.
5. After endpoint resolution, if `source_id == target_id` for any relation,
   the node SHALL reject the complete proposal before all `memory.create` and
   `memory.link` calls. The error SHALL identify that the self-relation was
   produced by candidate resolution/deduplication; an MCP `memory_link` call
   SHALL not be used as validation.
6. The preflight failure behavior SHALL be the same whether the common ID is an
   existing memory or would have been returned for a newly created canonical
   group.
7. The implementation SHALL retain a final defensive equality check immediately
   before linking. If it is reached, it SHALL fail safely without sending a
   self-edge; it SHALL not convert the MCP self-edge error into a successful
   update.

### Writes and atomicity boundary

8. For every deterministic preflight failure, the node SHALL make zero calls
   that mutate Memory MCP. In particular, no memory tagged with the current
   cycle may be created for a proposal containing a resolved self-edge.
9. For a valid proposal, the node SHALL create at most one memory per canonical
   new identity and SHALL return unique `created_memory_ids`. All `resolved_refs`
   SHALL point to actual IDs after creation.
10. The node SHALL preserve the current successful result contract, including
    `created_memory_ids`, `reused_memory_ids`, `created_relations`,
    `skipped_relations`, `resolved_refs`, and `context_reload_reason`.
11. The implementation SHALL NOT claim transactionality across separate MCP
    calls unless a transaction-capable MCP operation is added. If a create or
    link fails after preflight, successful prior calls may remain durable; the
    original failure SHALL be propagated and the update SHALL not be reported
    as successful.
12. No compensating archive, overwrite, or unrelated cleanup SHALL be used to
    disguise an operational failure as atomic rollback. Any stronger all-or-
    nothing guarantee requires an explicitly scoped MCP transaction/batch
    change, which is outside this proposal.

### Retry and Inbox behavior

13. Reapplying the same cycle and proposal after a partial operational write
    SHALL reconcile exact cycle tags and existing identities before creating
    anything. It SHALL not create a duplicate for an exact tagged candidate.
14. Reapplying an unchanged proposal that still contains a
    deduplication-induced self-relation SHALL fail at preflight again, without
    MCP writes. It SHALL not repeatedly call `memory_link` and SHALL not create
    a new cycle-tagged copy. A later retry can succeed only after the proposal
    no longer produces the invalid self-edge.
15. A regenerated proposal whose tagged candidate has changed title/content or
    category SHALL fail with the durable cycle-proposal conflict before any
    write. It SHALL not overwrite the tagged memory or create a replacement
    under the same `candidate_ref`.
16. When the node error reaches `SupervisorRuntime`, the existing failure path
    SHALL call `fail_batch` (or its current equivalent), SHALL not call
    `ack_batch`, and SHALL preserve the existing `PENDING`/`FAILED` behavior
    determined by Inbox attempt limits.

## Non-Functional Requirements

- **Determinism:** Preflight results SHALL depend only on the validated
  proposal and the loaded process context; no LLM call or MCP mutation may be
  used to decide whether a relation is a self-edge.
- **Safety:** A self-edge SHALL never be sent to Memory MCP. Error messages and
  observability data SHALL identify the operation and bounded candidate/error
  context without including credentials or full event payloads.
- **Idempotency:** Exact cycle retries SHALL converge on the same memory IDs and
  SHALL not increase the number of equivalent tagged memories.
- **Compatibility:** Existing valid deduplication, relation-skip, category-scope,
  and Inbox lease semantics SHALL remain unchanged.

## Affected Components

- `grams-app/supervisor/agent/nodes/apply_memory_update.py`: split or otherwise
  implement preflight and write phases while preserving the node result.
- `grams-app/tests/test_supervisor_v2_nodes.py`: unit tests for planned/existing
  ID collisions, no-write failures, and valid deduplicated relations.
- `grams-app/tests/test_latest_fixes_regressions.py`: cycle-tag retry and
  conflict tests, including a simulated partial MCP write.
- `grams-app/tests/test_supervisor_v2_resilience.py` or an equivalent runtime
  test location: verify failed apply cycles are not ACKed.
- `grams-app/memory-mcp/internal/memory/integration_test.go`: retain or extend
  the direct MCP invariant test that rejects self-edges if the MCP boundary is
  touched; no server-side relaxation is permitted.

## Constraints

- Memory MCP remains the durable memory authority; the Supervisor SHALL use the
  injected `MemoryClient` interface and SHALL not access SQLite directly.
- The current client exposes separate `create` and `link` operations and no
  rollback/delete operation. The implementer must respect that limitation.
- `validate_memory_proposal` already rejects literal self-relations and
  duplicate relation candidates. The new behavior is specifically for equality
  introduced after candidate/tag/identity resolution.
- Candidate references remain sequential `new_N` values and cycle tags remain
  `cycle_id:candidate_ref`; changing these contracts is out of scope.
- `memory_link` must continue to be called only for same-project, allowed,
  non-self relations.

## Edge Cases

- Two distinct candidate refs with identical category, title, and content map
  to one existing memory and are related to one another.
- Two distinct candidate refs with identical identity are both new and are
  related to one another; preflight must reject before the first create.
- One candidate matches an existing memory and another candidate with the same
  identity is new; both still resolve to the existing ID.
- A self-edge appears alongside otherwise valid candidates or valid relations;
  the complete proposal is rejected rather than partially applied.
- A cycle-tagged memory is present with exact identity, with changed identity,
  or with an identity that also matches another untagged candidate.
- A valid relation uses two distinct canonical groups that each contain
  multiple candidate refs; it remains linkable after group resolution.
- A retry occurs after a create succeeded but a later non-self link failed; the
  retry must reuse the durable tag and avoid a duplicate create.
- An unexpected MCP error occurs after one or more writes; the node reports
  failure and runtime ACK behavior remains failure-safe even though cross-call
  rollback is unavailable.

## Error Handling

- Schema, scope, unknown-endpoint, and post-resolution self-edge failures SHALL
  be deterministic validation failures and SHALL occur before MCP mutation.
- A post-resolution self-edge error SHOULD use a stable, testable message such
  as `memory update contains a self-relation after deduplication` and SHOULD
  include the affected endpoint/ref information in bounded form.
- A durable cycle-tag identity mismatch SHALL retain the existing explicit
  conflict meaning and SHALL occur during preflight, before any create/link.
- MCP transport, validation, or persistence errors after preflight SHALL
  propagate with the MCP operation identified. They SHALL not be swallowed,
  converted into a successful result, or hidden behind a self-edge workaround.
- The runtime SHALL fail the claimed batch rather than ACKing a graph execution
  that did not complete. Retry versus terminal failure remains governed by the
  existing Inbox attempt policy.

## Acceptance Criteria

1. A proposal with two distinct candidate refs that resolve to one ID and a
   relation between them fails before any `create` or `link` call; the error is
   a deterministic post-deduplication self-relation error, not
   `Memory MCP memory_link failed: invalid argument: self edge`.
2. The same no-write guarantee holds when the shared ID is an existing memory
   and when it would be a newly created memory; no cycle-tagged partial memory
   remains from either preflight failure.
3. A valid proposal still deduplicates equivalent candidates, creates each new
   identity once, resolves every ref, skips existing relations, and links only
   distinct endpoints.
4. An exact retry reuses any already tagged memory and does not duplicate it; a
   changed tagged candidate fails before all writes with the existing durable
   conflict.
5. A failed apply cycle is routed through Inbox failure handling and is never
   ACKed as processed; retry/terminal status follows the existing attempt limit.
6. Automated tests cover both collision modes, no writes on preflight failure,
   valid deduplication, exact retry reuse, conflict preflight, and runtime
   failure/ACK behavior. The existing Go MCP self-edge rejection remains green.

## Test Scenarios

1. **Existing-ID collision:** provide two `STRATEGY` candidates with identical
   normalized identity, an existing matching memory, and `new_1 -> new_2`.
   Assert the node raises the post-deduplication error and the fake memory has
   zero `create` and `link` calls.
2. **Planned-new collision:** provide the same candidates without an existing
   match. Assert preflight fails before `create`; in particular, no memory with
   `cycle_id:new_1` is present.
3. **Mixed existing/planned collision:** provide one candidate matching an
   existing memory and an equivalent candidate that would otherwise be new.
   Assert both refs resolve to the existing ID during preflight and no link is
   attempted for their relation.
4. **Self-edge beside valid work:** include a self-producing relation plus a
   separate valid candidate/relation. Assert the entire proposal is rejected
   and no valid portion is persisted.
5. **Successful grouped resolution:** retain the current
   `test_apply_memory_update_deduplicates_and_resolves_local_refs` behavior and
   add a case where two refs resolve to one new canonical memory while a
   different canonical memory is linked successfully.
6. **Exact retry after operational partial write:** use a fake Memory client
   that persists the first create and fails a later non-self link, reload a
   context containing the cycle tag, then retry the exact proposal with the
   link succeeding. Assert no duplicate create and a successful resolved result.
7. **Changed tagged proposal:** preload `cycle_id:new_1` with one identity and
   submit a proposal with a changed identity plus another candidate that would
   be new. Assert the durable conflict occurs before either candidate is
   created.
8. **Unchanged invalid retry:** submit the deduplication-induced self-edge
   proposal twice. Assert both attempts fail in preflight, neither sends
   `memory.link`, and the second attempt creates no additional memory.
9. **Runtime failure path:** run a cycle whose graph raises the apply
   validation error. Assert `fail_batch` is called, `ack_batch` is not called,
   and the Inbox event remains retryable or becomes terminal only according to
   the configured maximum attempts.
10. **MCP invariant regression:** retain/extend the Go service test asserting
    `CreateEdge` rejects equal source and target IDs; no client-side change may
    make the server accept self-edges.

## Out of Scope

- Automatically choosing which duplicate candidate should survive based on LLM
  confidence or semantic similarity beyond the existing deterministic identity.
- Silently dropping a self-relation while applying the rest of the proposal.
- Rewriting or deleting already durable memories created by an older failed
  deployment.
- Guaranteeing rollback for arbitrary failures after independent MCP writes
  without first introducing a transactional MCP contract.

## Assumptions

- Rejecting the complete proposal is the intended self-edge policy. This follows
  the existing `validate_memory_proposal` contract (`self-relations are not
  supported`) and avoids silently changing the model's proposed graph. If the
  product instead wants invalid self-relations omitted while other writes are
  retained, that is a materially different behavior and requires a separate
  decision and acceptance criteria.
- The current Supervisor retry path remains in force: graph exceptions fail the
  Inbox batch, and the Inbox decides whether the next attempt is `PENDING` or
  terminal `FAILED`.
- Full cross-call atomicity is not feasible through the currently exposed
  `MemoryClient`; the required atomicity guarantee is therefore the complete
  no-mutation preflight boundary, plus idempotent reconciliation for partial
  writes that did occur because of unrelated operational failures.
