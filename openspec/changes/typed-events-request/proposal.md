# Typed and validated `POST /events` request

## Objective

Require the `POST /events` receiver to validate a typed request envelope compatible with the envelope currently emitted by the GRAMS OpenCode plugin, while preserving the event payload unchanged for Inbox storage.

## Relevant Context

- `grams-opencode/opencode_plugin/src/index.ts` `normalize()` emits `schema_version: 1`, `type`, `source_event`, an ISO timestamp, `session_id` (possibly `null`), and `payload` containing the original value passed to `normalize()`.
- The current receiver (`grams-app/supervisor/api/events.py`) parses JSON, rejects non-finite JSON numbers/constants with HTTP 400, then delegates to `SupervisorEventInput.from_payload()` and persists. It currently accepts arbitrary JSON values; the converter defaults missing/invalid `type` to `UNKNOWN` and extracts optional metadata.
- `grams-app/supervisor/inbox/model.py` supports optional `id`, `root_session_id`, `run_id`, `instance_id`, and `sequence`. `source_run_id` is derived internally and is not an accepted input metadata field in the current model.
- Existing receiver tests explicitly cover arbitrary JSON acceptance and malformed JSON rejection; typed validation changes the former behavior.

## Scope

Specify request validation, field compatibility, and response behavior at `POST /events`. Do not specify changes to Inbox processing, event meaning, or plugin emission.

## Expected Behavior

Valid requests conforming to the plugin envelope are accepted and persisted. Invalid JSON remains distinct from valid JSON that fails envelope validation. Validated payload data is retained without projection or rewriting.

## Functional Requirements

1. The receiver SHALL accept a JSON object with required `schema_version`, `type`, `source_event`, `timestamp`, `session_id`, and `payload` fields.
2. `schema_version` SHALL be the integer `1`; `type` and `source_event` SHALL be non-empty strings; `timestamp` SHALL be a valid ISO-8601 timestamp compatible with the plugin's `Date.toISOString()` output; `session_id` SHALL be either a string or `null`.
3. `payload` SHALL be required but otherwise unconstrained JSON data (object, array, string, number, boolean, or null). The receiver SHALL preserve its complete value and structure in the persisted event envelope; it SHALL NOT replace it with extracted or normalized fields.
4. The receiver SHALL support these optional legacy metadata fields when present: `id`, `root_session_id`, `run_id`, and `instance_id` as strings; `sequence` as an integer. They SHALL be validated according to those types rather than silently coerced or ignored when malformed. `id` may be an empty string unless implementation establishes a stricter existing contract; no such restriction is evidenced by current code.
5. `schema_version`, `type`, `source_event`, `timestamp`, `session_id`, and `payload` SHALL not be treated as optional merely because the legacy converter currently supplies defaults.
6. Unknown top-level fields SHALL be accepted and ignored for typed field extraction, while the complete submitted envelope—including unknown fields—SHALL remain available in the preserved payload/envelope representation. (Do not reject future metadata solely for being unknown.)
7. Malformed JSON, invalid UTF-8, and non-standard/non-finite JSON numbers SHALL continue to return HTTP 400 with the existing malformed-body response behavior and SHALL not persist an event.
8. Syntactically valid JSON that is not an object or fails any envelope/field validation SHALL return HTTP 422 and SHALL not persist an event. It SHALL be distinguishable from malformed JSON (400).
9. A valid envelope SHALL continue to return HTTP 202 with an empty response body after successful Inbox persistence. Inbox persistence failures SHALL retain the existing HTTP 503 behavior.

## Non-Functional Requirements

- Validation SHALL be deterministic and shall not mutate the submitted `payload` value.
- Existing finite-JSON safeguards SHALL apply recursively, including within `payload` and unknown fields.

## Affected Components

- `grams-app/supervisor/api/events.py` and request schema/model boundary.
- `grams-app/tests/test_event_server.py` and related validation tests.
- Inbox input conversion only as needed to receive validated values without dropping payload data.

## Constraints

- Match the actual plugin envelope; do not require optional fields the plugin does not emit.
- `session_id: null` is intentional in `normalize()` when no session can be derived.
- Keep receiver durability and status semantics: persistence precedes 202.

## Edge Cases

- `payload` may itself be `null` or any other JSON type.
- `session_id: null` is valid; wrong scalar/container types are not.
- Optional integer `sequence` must not accept booleans as an integer despite Python's bool/int subtype relationship.
- Unknown keys must not cause schema rejection.
- Missing required fields, empty required strings, wrong schema version, malformed timestamp, and wrong optional metadata types are schema-invalid, not malformed JSON.

## Error Handling

- Return 400 for malformed JSON encoding/syntax or non-finite/non-standard JSON numbers.
- Return 422 for syntactically valid but schema-invalid JSON, with a response identifying request validation failure without exposing internal exceptions.
- Do not write invalid requests to the Inbox.
- Preserve current 503 response for unavailable Inbox persistence and 202 on successful persistence.

## Acceptance Criteria

- A request matching the plugin's `normalize()` output, including `session_id: null` and arbitrary JSON `payload`, receives 202 and persists the exact payload.
- Each required-field/type/version/timestamp violation receives 422 and creates no Inbox row.
- Each supported optional metadata field is accepted with its proper type; malformed optional values receive 422.
- Unknown top-level fields do not prevent acceptance and are not silently substituted for known fields.
- Malformed JSON and NaN/Infinity receive 400, distinct from 422 schema failures, with no Inbox row.
- Successful persistence still returns empty-body 202; persistence failure still returns 503.

## Test Scenarios

1. Submit a representative envelope with all required fields, nullable session, nested payload; assert 202 and stored payload equality.
2. Submit payload values of null, array, string, number, boolean, and object; assert preservation.
3. Omit each required field in turn; assert 422 and no persistence.
4. Test wrong schema version and invalid types, empty required strings, and invalid timestamps; assert 422.
5. Test each legacy optional field individually and together with valid and invalid types; ensure boolean `sequence` is rejected.
6. Include an unknown top-level field; assert request accepted and original envelope retained.
7. Submit malformed syntax, invalid UTF-8, and non-finite values; assert 400 and no persistence.
8. Assert persistence failures continue to return 503.

## Out of Scope

- Changing plugin normalization or event selection.
- Adding metadata fields not supported by the current receiver model, including `source_run_id` as an input field.
- Defining domain-specific validation for the contents of `payload`.
- Altering Inbox retry, lease, or worker behavior.

## Assumptions

- The requested typed schema applies to the complete top-level event envelope, with `payload` retaining its current opaque-data role.
- Existing optional metadata support means the five fields expressly supported by `SupervisorEventInput` (`id`, `root_session_id`, `run_id`, `instance_id`, `sequence`).
- OpenAPI/FastAPI validation may determine the precise 422 response body; the status distinction and no-persistence behavior are mandatory.
