# Pending interventions through OpenCode system transformation

## Objective

Replace `prompt_async` as the normal GRAMS intervention delivery mechanism. When
the Supervisor chooses `INTERVENE`, it must durably queue the intervention for
the target OpenCode session. Before the next model call, the OpenCode plugin
must retrieve that queued intervention through
`experimental.chat.system.transform`, append it to the first system-prompt
entry without removing the original prompt or using `output.system.push`, and
then acknowledge the intervention as consumed exactly once. The existing
`prompt_async` path remains available only as an explicit, safe fallback.

## Relevant Context

- The current Supervisor graph is
  `BUILD_INTERVENTION -> SEND_INTERVENTION -> RECORD_INTERVENTION -> FINALIZE`.
  `BUILD_INTERVENTION` generates the text after Jev has already selected
  `INTERVENE`; it must remain the only text-generation step.
- `send_intervention.py` currently stores a delivery marker in Memory MCP and
  calls `OpenCodeClient.send_message`. `send_message` delegates to
  `POST /session/{id}/prompt_async`.
- `OpenCodeClient._session_path` URL-escapes session IDs and its HTTP client
  already accepts successful responses with no body, including `204`.
  `send_message` must remain available for the fallback contract.
- `SupervisorState` contains transient `root_session_id`,
  `intervention_message`, and `intervention_result`. Durable event data belongs
  in SQLite, not in LangGraph state. `root_session_id` is the session identifier
  currently used by `SEND_INTERVENTION` when calling OpenCode.
- The FastAPI application currently exposes `POST /events` and initializes one
  SQLite connection/repository. The plugin currently has event, tool-before,
  and tool-after hooks, but no system-transform hook or intervention-read API.
- `GramsOpenCode.install` copies `grams-opencode/opencode_plugin/src/index.ts`
  into the trial and currently configures `GRAMS_EVENT_ENDPOINT`. The plugin
  therefore runs inside the OpenCode container while the Supervisor normally
  runs outside it.
- Existing tests assert direct `prompt_async` delivery in
  `test_supervisor_v2_nodes.py`, ambiguous-delivery reconciliation in
  `test_supervisor_v2_resilience.py`, and the client URL/body contract in
  `test_opencode_client.py`. Those assertions must be updated or retained
  according to the primary-versus-fallback behavior below.
- The earlier proposal
  `openspec/changes/supervisor-strong-nondestructive-intervention/proposal.md`
  describes `abort -> prompt_async` delivery. This change supersedes that
  proposal's normal delivery path: the default path queues and injects through
  the plugin and does not call `prompt_async` or abort the session.

## Scope

### Included

- A durable pending-intervention store associated with an exact OpenCode
  session ID.
- A Supervisor-side claim/consume contract that the plugin can call before a
  model request.
- Changes to the `INTERVENE` graph path so successful queueing is not reported
  as confirmed model injection.
- The plugin's `experimental.chat.system.transform` hook, including
  non-destructive first-system-entry mutation and one-shot consumption.
- Configuration/bootstrap needed for the plugin to reach the Supervisor's
  pending-intervention API.
- A deterministic compatibility fallback that retains the existing
  `prompt_async` client operation.
- Unit, API, integration, and failure tests for queueing, isolation, injection,
  consumption, retries, and fallback.

### Excluded

- Changing Jev thresholds, supervision prompts, intervention wording, or the
  policy that selects `INTERVENE`.
- Changing the Inbox event envelope, event normalization, event finalization,
  Memory MCP graph ontology, or process lifecycle semantics.
- Waiting for the OpenCode task to finish before acknowledging a successful
  intervention injection.
- Modifying OpenCode itself or adding a new OpenCode server endpoint.
- Removing `OpenCodeClient.send_message`, `inject_context`, or the
  `prompt_async` HTTP contract.

## Expected Behavior

For a new intervention targeting session `S` and message `M`, the normal flow
is:

```text
Jev action = INTERVENE
  -> BUILD_INTERVENTION produces M
  -> persist one durable PENDING record (session_id = S, message = M)
  -> finish the Supervisor cycle without calling prompt_async
  -> next OpenCode model call enters experimental.chat.system.transform
  -> plugin claims the oldest pending record for S
  -> plugin appends M to output.system[0]
  -> plugin acknowledges that record as CONSUMED
```

The Supervisor's successful result immediately after queueing is
`delivery_status = PENDING` (or an equivalent non-delivered status), not
`DELIVERED`. Only a successful plugin consume acknowledgment, or a successful
fallback `prompt_async` request, may produce `delivered = true`.

When no intervention is pending for the transform hook's session, the plugin
must leave the transform output unchanged. When an intervention is present,
the original value of `output.system[0]` must remain intact as a prefix and all
other system entries must remain unchanged. The implementation must mutate
index `0`; it must not remove the original entry and must not call
`output.system.push` (or replace `output.system` with a new array).

Consumption occurs after the append has succeeded. A consumed record is never
returned to a later transform call. A failed claim, failed append, or failed
consume acknowledgment must not be represented as confirmed delivery.

## Functional Requirements

### 1. Durable pending-intervention contract

1. When the selected action is `INTERVENE`, the Supervisor SHALL persist the
   exact non-empty intervention text before the cycle is acknowledged.
2. The record SHALL contain, at minimum, an opaque intervention ID, the exact
   target `session_id`, the exact message, the current intervention/cycle
   delivery key, creation time, and a status distinguishable as `PENDING`.
3. The pending record SHALL be stored in the configured durable SQLite
   database, or in a repository backed by that database. It SHALL NOT be held
   only in LangGraph state, a plugin-local map, or an in-memory Supervisor
   queue.
4. Enqueueing SHALL be idempotent for the existing intervention delivery key.
   A retry of the same Supervisor cycle must not create two pending records or
   replace the durable message with newly generated text.
5. The record SHALL remain associated with the same session ID through every
   state transition. A process ID, project ID, task ID, or newly-created
   OpenCode session SHALL not be substituted.
6. At least these semantic states SHALL be observable, whether or not these
   exact storage names are used:

   | State | Meaning |
   | --- | --- |
   | `PENDING` | Persisted and eligible for the target session's next transform. |
   | `CLAIMED`/`INJECTING` | Reserved by one transform invocation but not yet acknowledged as consumed. |
   | `CONSUMED` | The plugin appended the message and acknowledged it. |
   | `FALLBACK_DELIVERED` or equivalent | `prompt_async` accepted the message in the explicit fallback path. |
   | `DELIVERY_UNKNOWN` | The transport result cannot establish whether delivery occurred. |

7. A claim operation SHALL atomically reserve at most one eligible pending
   record for a given session. A claim SHALL return the record ID, session ID,
   message, and an opaque claim token; it SHALL return no record when none is
   eligible.
8. A consume operation SHALL require the record ID, the same session ID, and
   the valid claim token. It SHALL transition the record to `CONSUMED` only
   after the plugin has successfully mutated `output.system[0]`.
9. A repeated consume of an already consumed record SHALL not make the record
   eligible again or cause another injection. A claim lease/recovery mechanism
   MAY return an unconsumed claim to `PENDING` after a failed or abandoned
   transform, but a successfully `CONSUMED` record SHALL never be returned.
10. The Supervisor API used by the plugin SHALL enforce session scoping: a
    request for session `S1` must never return a pending message for `S2`.
    Invalid IDs, missing session IDs, and malformed request bodies SHALL be
    rejected without changing record state.

### 2. Supervisor `INTERVENE` path

11. `SEND_INTERVENTION` (or its replacement) SHALL enqueue the intervention as
    its primary side effect. After a successful enqueue, it SHALL NOT call
    `OpenCodeClient.send_message`, `inject_context`, or the
    `/prompt_async` endpoint.
12. The existing `BUILD_INTERVENTION` node SHALL continue to validate that the
    action is `INTERVENE` and SHALL remain the only component that generates
    the dynamic intervention text.
13. The intervention result SHALL distinguish queueing from injection. At
    minimum it SHALL expose the session ID, delivery key, pending record ID,
    queue status, and `delivered = false` while the record is pending.
14. A queue-success path MAY ACK the claimed Inbox cycle after the durable
    pending record and its queue audit are committed. That ACK SHALL mean that
    the Supervisor persisted the delivery work; it SHALL not mean that a model
    call has already received the message.
15. Existing intervention audit behavior SHALL not create a successful
    `DELIVERED`/injected evidence record merely because a `PENDING` record was
    created. If the current `RECORD_INTERVENTION` node remains in the graph, it
    SHALL record queueing as pending and SHALL be safe to rerun without
    duplicating the queue or falsely claiming injection.
16. If queue persistence fails, the graph SHALL fail according to the existing
    runtime/InBox failure path and SHALL not ACK the cycle as successfully
    finalized. A partially unknown database commit SHALL be reconciled by
    delivery key before another record or fallback request is created.
17. The target used by the current graph SHALL be the exact OpenCode session
    ID in `root_session_id`, and the plugin's `input.sessionID` SHALL match it
    for the current single-session deployment. If the implementation supports
    an event whose root and child session IDs differ, it SHALL choose an
    explicit target field or reject the intervention; it SHALL not silently
    mix the two identifiers.

### 3. Plugin system transformation

18. The plugin SHALL register
    `experimental.chat.system.transform` in addition to its existing hooks.
19. For each transform invocation, the plugin SHALL obtain the target session
    from the OpenCode hook input's `sessionID` and query the Supervisor pending
    intervention contract before the model call proceeds.
20. If no record is returned, the plugin SHALL leave `output.system` byte-for-
    byte/semantically unchanged and SHALL return normally.
21. For a record with message `M`, when `output.system[0]` is a string `P`, the
    plugin SHALL assign the first entry to `P + "\n\n" + M` (or an equivalent
    deterministic separator that preserves both strings exactly). The original
    prompt SHALL be retained as a prefix; no original system entry may be
    deleted, replaced by a new array, or reordered.
22. The plugin SHALL not invoke `output.system.push`, `splice`, `shift`,
    `pop`, or an equivalent operation that removes or appends a separate system
    entry for the intervention. The normal non-empty-array implementation must
    mutate `output.system[0]` directly.
23. System entries at indexes `1` and above SHALL remain unchanged. The array
    length SHALL remain unchanged when the standard `output.system[0]` entry
    exists.
24. If `output.system` is an empty array, the implementation MAY assign the
    intervention to index `0` because no original system prompt exists. If the
    output is malformed or the first entry cannot be safely preserved, the
    plugin SHALL not consume the record and SHALL report the transform failure
    through the configured observability path.
25. The plugin SHALL acknowledge consumption only after the assignment to
    `output.system[0]` has completed. It SHALL use the same intervention ID,
    claim token, and session ID returned by the claim operation.
26. The plugin SHALL handle an already consumed or unavailable record as no
    pending intervention and SHALL not inject a second copy.
27. Pending lookup, claim, and consume failures SHALL have bounded network
    behavior and SHALL not cause the original system prompt to be discarded.
    A failure must remain observable without turning an unmodified system
    prompt into a false successful intervention.

### 4. `prompt_async` fallback

28. The existing `OpenCodeClient.send_message(session_id, message)` contract
    SHALL remain available and SHALL continue to use
    `POST /session/{quote(session_id)}/prompt_async` with
    `{"parts":[{"type":"text","text":"<message>"}]}`.
29. The fallback SHALL not be the default path. A successful pending enqueue
    SHALL never be followed by an unconditional or parallel `prompt_async`
    call.
30. The fallback SHALL be selected only by an explicit compatibility mode or a
    deterministic failure branch that establishes that no committed pending
    record can later be injected. An unknown result from the enqueue operation
    SHALL be reconciled by delivery key/session before using the fallback;
    otherwise the fallback could duplicate a message that remains pending.
31. Before using the fallback for an intervention that already has a pending
    record, the implementation SHALL cancel/reconcile that record so the next
    transform cannot inject the same message as well.
32. The fallback SHALL use the same session ID and durable message as the
    pending record. A successful `2xx`, including `204`, MAY produce
    `delivered = true` with a fallback status. A timeout, disconnect, or
    rejected request SHALL not be reported as confirmed delivery.
33. The fallback SHALL not abort, close, delete, recreate, or otherwise stop
    the OpenCode session or Supervisor worker.

### 5. Bootstrap and observability

34. The plugin SHALL have a configured URL/base URL for the pending
    intervention API reachable from the OpenCode container. Existing
    `GRAMS_EVENT_ENDPOINT` event delivery SHALL continue unchanged.
35. The Harbor adapter SHALL propagate the pending API configuration when it
    installs the plugin, including the host/container address needed by the
    normal trial profile. The plugin SHALL fail safely when that configuration
    is absent rather than silently treating a failed lookup as consumed.
36. Logs/events SHALL distinguish at least `INTERVENTION_QUEUED`,
    `INTERVENTION_CLAIMED`, `INTERVENTION_CONSUMED`, `INTERVENTION_FALLBACK`,
    and delivery errors. They SHALL include a safe session identifier or
    correlation key and operation outcome, but SHALL not include credentials or
    the full intervention text.

## Non-Functional Requirements

- SQLite transitions for enqueue, claim, and consume SHALL be atomic under
  concurrent Supervisor/plugin requests.
- The pending record SHALL survive Supervisor/API restart and remain available
  to the correct session after restart.
- The transform hook SHALL preserve the OpenCode model-call path when there is
  no pending intervention or when the lookup is unavailable; it must not delete
  the original system prompt as an error-recovery action.
- The normal path SHALL add no unbounded polling loop or in-memory queue. One
  transform invocation should perform at most the lookup/claim and its matching
  consume acknowledgment.
- Session isolation, idempotency, and one-shot consumption SHALL be tested with
  concurrent or repeated requests, not only with a single happy-path call.
- The Supervisor's semantic decision and the plugin's eventual injection SHALL
  remain separately observable; queueing must never be mislabeled as injection.

## Affected Components

The implementation will likely affect:

- `grams-app/supervisor/platform/sqlite/db.py` and the SQLite repository layer,
  or a new pending-intervention repository backed by the same connection.
- `grams-app/supervisor/api/` and `grams-app/supervisor/app.py` for the
  session-scoped claim/consume API and lifecycle wiring.
- `grams-app/supervisor/agent/nodes/send_intervention.py`,
  `record_intervention.py`, `state.py`, and possibly `graph.py` for queue
  status, idempotency, and non-delivered audit semantics.
- `grams-app/supervisor/opencode/client.py` only as needed to preserve or make
  the fallback contract explicit; the existing `prompt_async` transport must
  remain available.
- `grams-opencode/opencode_plugin/src/index.ts` for the transform hook,
  pending lookup, first-entry mutation, and consume acknowledgment.
- `grams-opencode/grams_opencode.py` and deployment configuration for the
  pending API URL.
- `grams-app/tests/test_supervisor_v2_nodes.py`,
  `test_supervisor_v2_resilience.py`, `test_event_server.py`,
  `test_opencode_client.py`, `test_grams_opencode_adapter.py`, plus plugin
  tests or a small TypeScript/runtime harness.

## Constraints

- Do not put the pending intervention or its lifecycle solely in LangGraph
  state. SQLite is the durable operational source of truth.
- Do not use Memory MCP as the plugin's live pending queue. Memory MCP may
  retain semantic/audit evidence, but the plugin delivery record needs an
  atomic session-scoped claim/consume contract.
- Preserve `root_session_id`, the existing cycle/delivery-key deduplication,
  and the durable message on retries.
- Use the injected client/repository abstractions. The Supervisor node must not
  build ad hoc HTTP URLs, and the plugin must not assume access to the host's
  filesystem or SQLite file.
- Preserve the existing `POST /events` contract and event normalization.
- The intervention must not close the active Memory MCP process or OpenCode
  session. An intervention changes direction while the current process remains
  active.
- The plugin must be compatible with the pinned OpenCode version installed by
  `GramsOpenCode` and must use the documented hook name exactly:
  `experimental.chat.system.transform`.
- The first system prompt is modified in place. The implementation must not
  solve the requirement by adding a second system entry or by replacing the
  complete system-prompt array.

## Edge Cases

- Empty or invalid session ID/message: reject before persistence or external
  delivery; make no `prompt_async` call.
- Two Supervisor retries for the same cycle: one durable pending record and one
  eventual injection at most.
- Pending records for `S1` and `S2`: a transform for `S1` can see only `S1`'s
  record.
- Multiple pending records for one session: claim in creation order and inject
  at most one record per model-call transform.
- No pending record: preserve the exact system output and do not acknowledge an
  unrelated record.
- Empty `output.system`: assign index `0` only because there is no original
  system prompt; do not call `push`.
- Non-array or non-string first system entry: leave the output unchanged and
  keep the record unconsumed.
- Consume request lost after the output was mutated: do not call
  `prompt_async`; retry/reconcile the same consume operation using the same
  claim rather than injecting a second message in the same transform.
- Supervisor restart after enqueue and before the next model call: the pending
  record remains available.
- Plugin restart after claim but before consume: the claim must either be
  safely recoverable after its lease or remain inspectable as not consumed; it
  must not become `CONSUMED` without a successful append acknowledgment.
- Session IDs containing reserved URL characters: the API/client boundary must
  encode them safely and must not truncate or normalize them into another ID.
- Queue persistence succeeds but cycle finalization fails: retrying the cycle
  must reuse the existing delivery key and record, not enqueue a duplicate.
- Queue persistence has an unknown outcome: do not immediately use
  `prompt_async`; reconcile the durable record first.

## Error Handling

1. Validation errors must occur before queue writes and before fallback calls.
2. A failed durable enqueue must fail the Supervisor graph through the existing
   runtime path, leaving the Inbox claim unacknowledged/retryable.
3. A plugin lookup or claim failure must not consume a record or remove the
   original system prompt. It must be logged with a bounded error and allow the
   model-call hook to return safely.
4. A successful append followed by an unconfirmed consume must be reported as
   delivery-unknown, not consumed or fallback-delivered. The implementation
   must reconcile the same intervention ID before attempting another delivery.
5. A malformed consume request, wrong session, or wrong claim token must not
   change the record state.
6. A non-`2xx`/timeout/disconnection from fallback `prompt_async` must not set
   `delivered = true`. No application/client shutdown operation may be invoked
   as part of that failure.
7. Errors and logs must distinguish Supervisor decision, durable queueing,
   plugin injection, consumption acknowledgment, and fallback transport.

## Acceptance Criteria

1. A test that runs the `INTERVENE` path with a fake pending store observes one
   durable record for session `S` and message `M`, and observes zero calls to
   `send_message`/`prompt_async` on the successful default path.
2. Replaying the same cycle/delivery key returns the existing record and does
   not create a second pending record or replace its message.
3. A session-scoped claim for `S1` never returns an intervention for `S2`, and
   concurrent claims cannot return the same pending record to two consumers.
4. The plugin transform test with original systems `[P, Q]` produces a first
   entry beginning with exactly `P`, containing `M` once, leaves `Q` unchanged,
   preserves array length, and does not execute `output.system.push`.
5. A transform with no pending record leaves `output.system` unchanged.
6. After a successful append, the plugin sends the matching consume
   acknowledgment; a subsequent transform for the same session does not inject
   the message again.
7. If append/claim/consume fails before confirmed consumption, the record is
   not reported as consumed and the original system prompt is not deleted.
8. Pending state survives a Supervisor restart and is still retrievable only
   for its associated session.
9. The fallback test proves that `prompt_async` is still available, uses the
   exact same session ID and durable message, accepts `204`, and is not invoked
   after a successful pending enqueue. An ambiguous enqueue result is not
   followed by an uncoordinated fallback call.
10. The Supervisor result and any Memory MCP audit distinguish `PENDING`/
    `QUEUED` from `CONSUMED`/delivered; queue creation alone cannot pass the
    current delivered-only audit guard.
11. Existing `POST /events`, event normalization, Inbox leasing, and non-
    interventive routes continue to pass their existing tests.
12. No test or implementation path calls session abort, session deletion,
    session creation, `OpenCodeClient.aclose`, worker shutdown, or FastAPI
    shutdown as part of normal pending intervention delivery.

## Test Scenarios

1. **Queue happy path:** build an `INTERVENE` state, persist it, inspect the
   SQLite record, and assert no `prompt_async` call.
2. **Queue idempotency:** execute the same delivery key twice with different
   newly generated text and verify one record containing the first durable text.
3. **API contract:** exercise claim, no-record, consume, repeated-consume,
   malformed-request, wrong-session, and wrong-token responses.
4. **Concurrent claim:** issue two claims for one session and verify only one
   receives the record.
5. **Transform append:** use `["original system", "second system"]`, inject one
   message, and verify prefix preservation, unchanged second entry, unchanged
   length, and no `push` invocation.
6. **No-op transform:** use a session without a pending record and verify no
   network consume call and no output mutation.
7. **Empty/malformed output:** verify the documented index-zero behavior for an
   empty array and safe non-consumption for malformed output.
8. **One-shot injection:** invoke the transform twice and verify the message
   appears only once and the store ends in `CONSUMED`.
9. **Restart/recovery:** enqueue, close/reopen the SQLite-backed application,
   and claim the still-pending record for the same session.
10. **Consume failure:** make the consume acknowledgment fail after mutation;
    verify no fallback call is made and the same record is reconciled rather
    than blindly injected again.
11. **Fallback mode:** disable/unavailable the primary mechanism through the
    explicit fallback branch and verify the existing `prompt_async` body,
    escaped session path, `204` handling, and fallback status.
12. **Supervisor failure/ACK:** fail enqueue and verify `fail_batch`/retry with
    no false Inbox ACK; separately verify queue success can finalize without
    claiming that injection already occurred.
13. **Adapter/bootstrap:** verify the installed plugin receives both the
    existing event endpoint and the pending-intervention endpoint/configuration.
14. **Non-intervention routes:** run `CONTINUE`, `NEED_MORE_MEMORY`, and
    `CLOSE_PROCESS` with spies that fail if they enqueue, claim, or call
    `prompt_async`.

## Out of Scope

- Automatic recalibration of intervention decisions.
- A new model, prompt, or intervention-message format beyond preserving the
  generated text during delivery.
- Cross-session broadcasting, multi-agent fan-out, or a new OpenCode session.
- Guaranteed completion of the OpenCode task after injection.
- Replacing the existing event Inbox with the pending-intervention store.
- A time-based policy for deciding that a queued intervention has failed; such
  a policy would need an explicit lease/timeout product requirement.

## Assumptions

- In the current deployment, `root_session_id` is the OpenCode `sessionID`
  targeted by an intervention. The implementation must make any divergence
  explicit rather than silently mapping IDs.
- The Supervisor and plugin can communicate through a small HTTP API exposed by
  the existing FastAPI application. The exact route names may follow local API
  conventions, but the claim/consume semantics in this specification are
  mandatory.
- The pending store is operational delivery state, so SQLite is the appropriate
  durable source; Memory MCP remains the semantic/audit graph source.
- `experimental.chat.system.transform` is available in the pinned OpenCode
  version used by `GramsOpenCode`. If the pinned version does not provide this
  hook, the compatibility fallback must be selected explicitly and the version
  support gap must be reported.
- “One-shot” means that a successfully consumed record is never returned again.
  A lease recovery may retry a record that was claimed but never acknowledged,
  because an exact-once guarantee across a process crash cannot be established
  by a post-mutation HTTP acknowledgment alone.

## Ambiguity Requiring Confirmation

The request specifies that `prompt_async` remains a fallback but does not define
the trigger for automatic fallback after a pending record has been committed.
Automatically sending `prompt_async` after a lookup timeout or a lost consume
response can duplicate an intervention that is still pending or was already
injected. This specification therefore requires an explicit compatibility mode
or a failure branch that can prove no pending record was committed, and forbids
an uncoordinated time-based fallback. If product behavior requires automatic
failover after a committed pending record, the implementation needs an
additional cancellation/expiry and reconciliation contract before that behavior
can be safely specified.
