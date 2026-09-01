GRAMS — OpenCode Receiver Mini-Architecture V0

Purpose: Define the minimal OpenCode observation layer used by GRAMS before information reaches the Memory Supervisor and Graph Memory.

1. Main Session

GRAMS continuously evaluates the main OpenCode session.

MAIN SESSION
     │
     ├── REASONING
     │     What is the agent thinking or trying to discover?
     │
     ├── TEXT
     │     What does it explicitly conclude or plan?
     │
     ├── TOOL_CALL
     │     What action is it about to perform?
     │
     ├── TOOL_RESULT
     │     What evidence or result did the action produce?
     │
     ├── FILE / STATE CHANGE
     │     What changed in the environment or implementation?
     │
     └── SESSION / TASK
           Is work being delegated to a new or resumed subagent?

These signals are continuously interpreted by the Memory Supervisor.

Only information with expected future utility is persisted into Graph Memory.

2. Subagent Task Gate

Subagents are treated differently.

GRAMS does not continuously analyze the full internal trajectory of a subagent.

Before the subagent runs, GRAMS evaluates the purpose of the task call against Graph Memory.

MAIN AGENT
    │
    │ task(objective)
    ▼
GRAMS TASK GATE
    │
    ├── NOVEL
    │     No relevant prior knowledge.
    │     → Run subagent normally.
    │
    ├── PARTIALLY_KNOWN
    │     Part of the objective is already known.
    │     → Inject compact relevant knowledge.
    │     → Run subagent normally.
    │
    └── RESOLVED
          Objective is already sufficiently solved.
          → Do not run / interrupt the task.
          → Return the known result to the main agent.

3. Subagent Observation Policy

Once a valid subagent is running, GRAMS does not inspect every reasoning step, tool call, or intermediate discovery.

SUBAGENT
    │
    ├── internal reasoning      ┐
    ├── internal tool calls     │ not continuously reviewed
    ├── intermediate results    │ by GRAMS V0
    └── internal exploration    ┘
    │
    ▼
FINAL TASK RESULT
    │
    ▼
GRAMS
    │
    ├── evaluate result
    ├── extract useful knowledge
    └── persist relevant memories / relations

For PARTIALLY_KNOWN tasks, the subagent receives only the relevant confirmed information required to avoid rebuilding already-known prerequisites.

4. Minimal Extra Metadata

Even without observing the complete child trajectory, GRAMS should retain:

task_id / child_session_id
parent_session_id
task objective
classification: NOVEL | PARTIALLY_KNOWN | RESOLVED
completion status: success | error | aborted
final task result

This preserves provenance and prevents a failed or aborted subagent result from being treated as confirmed knowledge.

If a subagent is allowed to modify files or environment state, GRAMS should additionally capture its final side effects / diff. If subagents are strictly read-only, this is unnecessary.

5. Complete Receiver Flow

                         OPENCODE
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
         MAIN SESSION                 TASK CALL
              │                           │
              │                           ▼
              │                    GRAMS TASK GATE
              │                    /      |       \
              │                 NOVEL   PARTIAL   RESOLVED
              │                   │        │         │
              │                   │        │         └─► return Graph result
              │                   │        │
              │                   │        └─► inject relevant memory
              │                   │
              │                   └────────┬─────────┘
              │                            ▼
              │                         SUBAGENT
              │                            │
              │                       final result
              │                            │
              └──────────────┬─────────────┘
                             ▼
                     MEMORY SUPERVISOR
                             │
                    extract / update / link
                             │
                            MCP
                             │
                             ▼
                        GRAPH MEMORY

6. V0 Principle

The main agent is continuously supervised. Subagents are gated before execution and evaluated from their final result, not continuously supervised internally.

For subagents:

NOVEL → run normally.

PARTIALLY_KNOWN → inject only relevant confirmed knowledge, then run normally.

RESOLVED → suppress/interrupt the task and return the already-known result to the main agent.

The Graph Memory remains the persistent shared knowledge source for the whole execution.

7. Technical V0 Implementation

The current integration is split between the Harbor adapter and the OpenCode
plugin:

```text
 grams_opencode/
├── __init__.py                      Public GramsOpenCode export
├── grams_opencode.py                Harbor adapter / trial bootstrap
└── opencode_plugin/
    ├── package.json
    └── src/index.ts                  OpenCode plugin and event normalizer
```

Harbor job configurations import `GramsOpenCode` directly from
`grams_opencode.grams_opencode`.

The Harbor adapter runs during trial setup. It installs the pinned OpenCode
version, writes the plugin into the trial's
`~/.config/opencode/plugins/grams-receiver.ts` directory, and configures the
`GRAMS_EVENT_ENDPOINT` environment variable. OpenCode then loads the local
plugin using its standard plugin discovery mechanism. The adapter does not
interpret OpenCode events itself.

The plugin runs in the same trial container as OpenCode. Its source is kept in
the repository under `grams_opencode/opencode_plugin`, while the adapter
embeds and writes that source into the trial at setup time. This is required
because a containerized OpenCode process cannot load a plugin that exists only
on the host unless the source is explicitly mounted or copied into the trial.

The plugin currently registers these OpenCode hooks:

- `event`, for the OpenCode event bus;
- `tool.execute.before`, for tool-call observation before execution;
- `tool.execute.after`, for tool-result observation after execution.

The receiver intentionally does not forward the event stream one event at a
time. It ignores message deltas, tool-start events, and intermediate part
updates. It retains the latest text/reasoning part in memory and emits only
`USER_MESSAGE_FINAL`, `TEXT_FINAL`, or `REASONING_FINAL` when the part has
ended, or when `session.idle` forces the final flush. It emits one
`TOOL_CALL_FINAL` for the complete tool intent and one `TOOL_RESULT_FINAL`
after `tool.execute.after`, and emits `MESSAGE_COMPLETED` on `session.idle`.
This preserves the causal context without making the Memory Supervisor
process token-level or tool-progress events.

The normalizer maps native events to GRAMS event types, including:

```text
message.part.updated (text)       → TEXT
message.part.updated (reasoning)  → REASONING
tool.execute.before               → TOOL_CALL
tool.execute.after                → TOOL_RESULT
file.edited                       → FILE_CHANGE
session.*                         → corresponding SESSION_* event
```

Every normalized event includes `schema_version`, `type`, `source_event`, a
timestamp, the session identifier when available, and the original payload.
This preserves the native event while providing a stable GRAMS envelope.

For the current receiver smoke test, the plugin sends JSON events using HTTP
`POST` to `/events`. The local Python receiver is implemented in:

```text
grams-app/supervisor/event_server.py
```

The receiver only prints each JSON event. It is intentionally not yet a
Memory Supervisor and does not classify, persist, or modify events.

During local development, Harbor and OpenCode run in Docker while the Python
receiver runs on the host. Docker Desktop/Colima exposes the host receiver to
the trial through:

```text
http://host.docker.internal:8765/events
```

The integration test at:

```text
grams-app/tests/receptor-opencode/run_test.py
```

starts the local receiver automatically and runs a small Harbor task. The
test is intended to verify that OpenCode loads the plugin and that normalized
events reach the host process.

The following pieces are deliberately not implemented yet:

- Memory Supervisor decisions;
- Graph Memory or MCP operations;
- `NOVEL`, `PARTIALLY_KNOWN`, and `RESOLVED` classification;
- task-call modification or suppression;
- persistent trace storage;
- a separate TypeScript launcher shim.

The `tool.execute.before` hook is reserved for the future task gate. Its
current role is observation only.
