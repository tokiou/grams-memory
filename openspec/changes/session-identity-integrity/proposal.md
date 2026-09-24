# Reliable session identity for supervised events and interventions

## Objective

Prevent events that require OpenCode session context from being silently assigned to a fabricated `default` session. Accept or quarantine such events unless a trustworthy session identity is available, and ensure interventions are delivered only to the actual correlated OpenCode session.

## Relevant Context

- The OpenCode plugin normalizes events into a `session_id` field. Its current extraction checks `event.sessionID` and selected nested `sessionID` properties; an OpenCode `file.edited` payload shaped as `{id, type, properties: {file}}` has no such field and results in `session_id: null`.
- `POST /events` currently permits nullable session IDs. `SupervisorEventInput.from_payload()` maps absent identity to `root_session_id='default'`.
- Supervisor decisions and pending interventions use the resulting root session. The synthetic `default` does not match the real OpenCode session making the later session-scoped intervention request, so the intervention is not delivered; querying `/session/default/message` can fail with HTTP 500.
- Existing event normalization and receiver contracts must be consulted when changing accepted/quarantined event behavior; valid session-bearing events already flow through the Inbox.

## Scope

Specify trustworthy session identity across plugin normalization, event ingress, Inbox conversion, decision-making, and intervention targeting. Include regression coverage at unit, API, and integration levels. Do not prescribe a particular implementation or session-correlation mechanism.

## Expected Behavior

Events requiring session context are processed only when associated with a trustworthy session identity. Missing or untrusted identity is rejected or quarantined before it can cause a Supervisor decision or intervention. `file.edited` is attributed only when explicit, trustworthy correlation establishes its session; the system does not guess based on a recent/active session or select among concurrent sessions. Valid events with a trustworthy session retain their existing processing and delivery behavior.

## Functional Requirements

1. The system SHALL NOT convert a missing or null session identity into the literal session ID `default` (or another invented identity) for events requiring session context.
2. Ingress SHALL reject or quarantine an event that requires session context when it has no trustworthy session identity. It SHALL NOT persist/process such an event in a way that can produce a session-scoped decision or intervention under a fabricated identity.
3. The event contract SHALL distinguish a genuinely absent/untrusted identity from a valid session ID. A valid session ID SHALL be preserved without substitution through Inbox conversion and Supervisor processing.
4. A `file.edited` event SHALL be associated with a session only through explicit, trustworthy session metadata or an explicit reliable correlation to that session. It SHALL NOT infer identity from recency, a global active session, or ambiguous concurrent activity.
5. The Supervisor SHALL NOT create or enqueue a session-targeted intervention for `default` or any other fabricated session identity. A decision requiring session context SHALL be withheld or safely handled when identity is unavailable.
6. An intervention generated for a valid event SHALL retain the same verified session identity through decision, queueing, and plugin retrieval; only that session may receive the intervention.
7. Existing behavior for otherwise valid events carrying a trustworthy session ID SHALL remain intact, including Inbox processing and correct-session intervention delivery.

## Non-Functional Requirements

- Session attribution SHALL be deterministic and safe under concurrent OpenCode sessions.
- Rejection or quarantine SHALL not silently discard the identity failure: it SHALL be observable through the existing error/diagnostic conventions without exposing sensitive data.

## Affected Components

- `grams-opencode/opencode_plugin/src/index.ts` event normalization and `file.edited` event handling.
- `grams-app/supervisor/api/` request validation and event ingress behavior.
- `grams-app/supervisor/inbox/model.py` input conversion and session identity semantics.
- Supervisor decision/intervention targeting paths that consume Inbox events.
- Plugin, API, Inbox, and Supervisor integration tests.

## Constraints

- Do not use `default` as a fallback for missing session identity.
- Do not introduce cross-session guessing or attribution from concurrent activity.
- Preserve the existing behavior for valid session-bearing events and the Inbox as the durable event source.
- The specification leaves the exact reliable correlation source and the choice between rejection and quarantine to implementation, provided both satisfy these safety requirements.

## Edge Cases

- `file.edited` payload contains only `{id, type, properties: {file}}` and no session metadata.
- Session field is explicitly null, empty, malformed, or otherwise untrusted.
- Multiple sessions are active concurrently and a file event lacks an explicit correlation.
- A valid session-bearing event is followed by an event without identity; the latter must not inherit the former's session merely because it was recent.
- An event with a valid session produces an intervention while other sessions are active; delivery must remain isolated to its target.

## Error Handling

- Missing/untrusted identity for a context-dependent event SHALL lead to a clear rejection or quarantine outcome before decision/intervention processing.
- Such an outcome SHALL not be represented as a successful event acceptance that later produces `default`-scoped work.
- Valid identified events SHALL continue through normal processing; unrelated errors and retry behavior remain governed by existing contracts.

## Acceptance Criteria

1. A `file.edited` event with no session identity cannot create an Inbox item that becomes a decision or intervention for `default`.
2. A context-dependent event with absent, null, empty, or invalid identity is observably rejected or quarantined, and creates no fabricated-session decision/intervention.
3. With two concurrent sessions and an uncorrelated `file.edited` payload, neither session is guessed or selected.
4. A valid event for session `S` can produce an intervention targeted to `S`; retrieval by `S` returns it, while retrieval by another session does not.
5. Existing valid session-bearing events continue to be accepted and processed according to current behavior.

## Test Scenarios

1. **Plugin/unit regression:** normalize `file.edited` with `{id, type, properties: {file}}`; assert it does not claim a session through inference and that the event is marked/handled as lacking trustworthy session context.
2. **Plugin/unit regression:** normalize a `file.edited` event with explicit trustworthy session metadata; assert the exact session ID is retained.
3. **API regression:** submit an event requiring session context with `session_id: null` or absent; assert the defined reject/quarantine result and verify no processing-capable fabricated-session Inbox event is created.
4. **Inbox/Supervisor unit regression:** convert/process an identity-less event and assert no `root_session_id='default'`, decision, or intervention is produced.
5. **Concurrency integration:** create concurrent sessions and submit an uncorrelated `file.edited`; assert no session receives attribution.
6. **Intervention integration:** submit a valid session-bearing event for `S`, drive it through Inbox and intervention queueing, then assert the intervention is retrievable/deliverable for `S` and not for another session; assert no request is made to `/session/default/message`.
7. **Compatibility regression:** process a valid identified event through the existing path and assert successful Inbox handling and session-correct behavior.

## Out of Scope

- Redesigning intervention policy, wording, or delivery mechanics.
- Adding heuristic or probabilistic attribution for events without trustworthy correlation.
- Changing event types that do not require session context unless needed to prevent fabricated identity.
- General API schema redesign unrelated to session identity.

## Assumptions

- `file.edited` requires session context when it can influence a Supervisor decision or intervention.
- The reported `default` mapping and mismatch between the queued session and the plugin's real session are the confirmed failure chain described in the request.
- Either rejection or quarantine is acceptable to the product, but the selected behavior must be explicit, observable, and prevent downstream decisions/interventions.
