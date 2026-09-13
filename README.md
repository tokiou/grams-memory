# GRAMS

GRAMS currently provides durable event transport between the OpenCode plugin
and an SQLite Inbox. The standalone Go Memory MCP remains available as a
separate component, but the receiver does not invoke it yet. Agentic review,
intervention, and lifecycle logic are intentionally not part of this baseline.

## Architecture

```text
OpenCode plugin
      |
      | POST /events
      v
FastAPI receiver
      |
      v
SQLite Inbox
```

The receiver validates JSON, normalizes the event input, and persists it before
returning `202`. The Inbox owns event status, leasing, retries, and acknowledgements.

## Components

- `grams-opencode/opencode_plugin/`: sends normalized OpenCode events.
- `grams-app/supervisor/supervisor/api/`: HTTP event ingress.
- `grams-app/supervisor/supervisor/inbox/`: durable event journal and lease operations.
- `grams-app/supervisor/supervisor/platform/sqlite/`: SQLite connection setup.
- `grams-app/supervisor/supervisor/observability.py`: structured logging helpers.

The following are deliberately absent from the receiver baseline:

- LangGraph agent state and routing.
- Review models and prompts.
- Supervisor worker/runtime and process lifecycle.

The standalone Memory MCP server is retained under `grams-app/memory-mcp/`.
It provides graph memory over Streamable HTTP and can be started independently:

```bash
go run ./grams-app/memory-mcp/cmd/server
```

The Python integration clients are also retained under
`grams-app/supervisor/supervisor/memory/` and
`grams-app/supervisor/supervisor/opencode/`. They are currently unused by the
receiver-only bootstrap and remain available for the next agent design.

## Local Setup

Install Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Start the receiver:

```bash
.venv/bin/uvicorn supervisor.app:app \
  --app-dir grams-app/supervisor \
  --host 0.0.0.0 \
  --port 8765
```

The database path defaults to:

```text
~/Library/Application Support/grams/supervisor.db
```

Override it with `GRAMS_SUPERVISOR_DB_PATH` or `GRAMS_DB_PATH`.

## Event Contract

The receiver exposes one route:

```text
POST /events
```

Valid JSON is persisted and returns `202` with an empty response body. Invalid
JSON returns `400`. If the Inbox or SQLite database is unavailable, the receiver
returns `503`.

The plugin endpoint is configured with:

```text
GRAMS_EVENT_ENDPOINT=http://127.0.0.1:8765/events
```

## Verification

```bash
pytest -q grams-app/tests/test_event_server.py grams-app/tests/test_observability.py
git diff --check
```
