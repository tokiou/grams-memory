# GRAMS Development Guide

This file documents the current repository architecture and the local protocol
for working with OpenCode, Harbor, and GRAMS.

## Development Standards

- Follow SOLID principles when designing and implementing code.
- In Python, use `typing.Protocol` for interfaces and contracts instead of
  unnecessary base classes or inheritance.
- Keep transport, orchestration, memory, and action-agent integration as
  separate responsibilities.
- Do not put durable event data in LangGraph state. The Inbox is the durable
  source of execution events.

## Architecture

GRAMS has two layers:

1. Durable event transport, implemented now.
2. Supervisor-managed relational execution memory, described by the v1 design.

The current transport is:

```text
OpenCode
  -> OpenCode plugin
  -> POST /events
  -> FastAPI receiver
  -> SQLite Inbox
```

The intended v1 Supervisor is:

```text
SQLite Inbox
  -> Supervisor Runtime
  -> LangGraph orchestration
  -> current process graph from Memory MCP
  -> review / memory update / intervention
```

The Action Agent remains focused on solving the task. It does not own long-term
memory. The Supervisor owns process identification, strategy and evidence
memory, typed relations, graph retrieval, and memory-conditioned decisions.

## Current Implementation

Implemented transport components:

- `grams-opencode/opencode_plugin/`: normalizes and sends OpenCode events.
- `grams-app/supervisor/api/`: validates and receives `POST /events`.
- `grams-app/supervisor/inbox/`: durable event journal, leases,
  retries, claims, and acknowledgements.
- `grams-app/supervisor/platform/sqlite/`: SQLite setup.
- `grams-app/supervisor/memory/`: retained Python MCP client.
- `grams-app/supervisor/opencode/`: retained Python OpenCode client.
- `grams-app/memory-mcp/`: retained standalone Go graph-memory MCP server.

The following agentic components are intentionally not implemented in the
current baseline and must be rebuilt for v1:

- Supervisor worker/runtime.
- LangGraph state and graph topology.
- Review model and prompts.
- Process lifecycle orchestration.
- Memory update and graph-context nodes.
- Intervention policy.

## Event Inbox Contract

`POST /events` persists an event before returning `202`. Events are stored in
the `supervisor_events` SQLite table with status `PENDING`.

The durable flow for a future consumer is:

```text
claim_pending -> PROCESSING -> ack -> PROCESSED
                              \-> fail -> PENDING or FAILED
```

LangGraph nodes must consume events through `EventInbox`, not by maintaining a
second in-memory event queue. Claimed events may be held in memory during one
graph invocation, but SQLite remains the source of truth.

The default database path is:

```text
~/Library/Application Support/grams/supervisor.db
```

Override it with `GRAMS_SUPERVISOR_DB_PATH` or `GRAMS_DB_PATH`.

## v1 Memory Principles

- Every supervised task has one Project scope.
- Each meaningful execution strategy is represented as a process Key.
- Each process uses `strategy`, `evidence`, and `summary` categories.
- Normal reviews consume a compact current-process graph view.
- Deeper graph expansion is triggered by operational signals, not directly by
  elapsed time alone.
- A process closes only when accumulated evidence supports success, failure,
  abandonment, or supersession.
- Closing a process writes or updates its Summary.
- Starting a new process preserves its relation to the previous process.
- Relations are decision-relevant graph data, not merely storage metadata.

The full v1 direction is documented in `ARCHITECTURE.md` and
`docs/v1/README.md`.

## Local Services

The receiver runs on port `8765`:

```bash
./scripts/run_supervisor.sh
```

Use the launcher so the host Supervisor receives the credentials from `.env`
used by the JEV and OpenRouter clients.

The standalone Go Memory MCP runs independently:

```bash
go run ./grams-app/memory-mcp/cmd/server
```

For Harbor trials, the OpenCode control server uses host port `4096` and the
plugin sends events to:

```text
http://host.docker.internal:8765/events
```

Only one trial may run at a time with the current fixed port mapping.

## Trial Profile

The standard long-running evaluation profile is:

- Docker trial CPU: `4` CPUs;
- Docker trial memory: `8192 MB`;
- Harbor concurrency: `1` trial;
- timeout multiplier: `1.0`;
- receiver: host `uvicorn`, port `8765`.

Use the repository wrapper for supervised Harbor trials. Do not launch another
trial while the fixed `4096` host port is in use.

## Verification

Receiver and retained MCP-client tests:

```bash
pytest -q \
  grams-app/tests/test_event_server.py \
  grams-app/tests/test_memory_client.py \
  grams-app/tests/test_observability.py
```

Go MCP tests:

```bash
go test ./...
```

All changes must pass:

```bash
git diff --check
```
