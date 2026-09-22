# GRAMS

GRAMS is a Supervisor-managed relational execution memory system for long-running
agent tasks. An Action Agent such as OpenCode performs the task; the Supervisor
uses durable execution events and graph-backed process memory to guide
continuation, retrieval, intervention, and process closure.

## Architecture

```text
OpenCode plugin -> FastAPI receiver -> SQLite Inbox
                                      |
                                      v
                               Supervisor worker
                                      |
                                      v
                                  LangGraph
                       Jev decisions / OpenRouter text
                                      |
                                      v
                            Memory MCP graph store
```

The Inbox is the durable source of event data. LangGraph state is temporary to
an execution cycle. The Supervisor reads and claims Inbox events, identifies
the active process, loads its Memory MCP context, updates strategy and evidence
memories, evaluates the execution, and applies deterministic routing and
side-effect policies.

Jev supplies bounded structured decisions. OpenRouter generates memory text,
process summaries, and intervention messages. Python orchestration and Memory
MCP own validation, persistence, process lifecycle, and side effects.

## Repository map

- `grams-app/supervisor/` — FastAPI receiver, SQLite Inbox, worker, LangGraph
  runtime, model clients, Memory MCP client, and OpenCode client.
- `grams-app/memory-mcp/` — standalone Go graph-memory MCP server.
- `grams-opencode/` — Harbor adapter and OpenCode event plugin.
- `docs/` — architecture and design documents.
- `openspec/` — change proposals, specifications, and observations.
- `scripts/` — local service and supervised Harbor launchers.

## Local development

Create a virtual environment, install the Python dependencies, and make a local
environment file from the template:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Set the required Jev and OpenRouter credentials in `.env`. Start the standalone
Memory MCP in one terminal:

```bash
cd grams-app/memory-mcp
go run ./cmd/server
```

Start the Supervisor receiver and worker in another terminal from the repository
root:

```bash
./scripts/run_supervisor.sh
```

The receiver listens on port `8765` by default. The worker is enabled by the
repository launcher and consumes durable Inbox events. The default SQLite
database is `~/Library/Application Support/grams/supervisor.db`; configure a
different path with `GRAMS_SUPERVISOR_DB_PATH` or `GRAMS_DB_PATH`.

## Verification

Run the Python receiver, Supervisor, and integration tests with:

```bash
pytest -q grams-app/tests
```

Run Go MCP tests from the module directory:

```bash
cd grams-app/memory-mcp
go test ./...
```

Before submitting changes, also run:

```bash
git diff --check
```

## Harbor trials

Use the repository's Harbor wrapper and trial profile for supervised runs.
Trials use a fixed host port `4096` for the OpenCode control server and port
`8765` for the receiver, so run one trial at a time. The Harbor adapter pins its
own OpenCode version for plugin and event-hook compatibility; updating the
interactive CLI installed on the host does not change that container pin.

## Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — system architecture, model boundary,
  runtime components, and setup.
- [`docs/v1/README.md`](docs/v1/README.md) — process memory and Supervisor
  behavior.
- [`docs/v2/JEV_OPENROUTER.md`](docs/v2/JEV_OPENROUTER.md) — Jev/OpenRouter
  model boundary and v2 graph flow.
- [`docs/v1/GRAPH.md`](docs/v1/GRAPH.md) — graph and memory design details.
- [`docs/v0/`](docs/v0/) — historical design documents.
