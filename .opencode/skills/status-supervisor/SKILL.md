---
name: status-supervisor
description: Use when the user asks for status, supervisor status, JEV status, trial status, or how OpenCode is progressing. Report Supervisor activity, decisions, interventions, intervention impact, and elapsed minutes from live evidence.
---

# Supervisor Status

Use this skill whenever the user asks for `status`, especially in the GRAMS
repository while a Harbor, OpenCode, or Supervisor trial is running.

## Required Report

Always produce a concise report in Spanish with these sections, in this order:

### Supervisor

Report:

- Whether the Supervisor process is running, including PID when available.
- Whether the worker is started and processing events.
- Current Inbox counts for the relevant `root_session_id`: `PENDING`,
  `PROCESSING`, `PROCESSED`, and `FAILED`.
- Whether the latest JEV/OpenRouter calls succeeded or failed.

### Qué Hizo

Summarize concrete actions from recent logs, not assumptions. Include recent
JEV decisions such as `CONTINUE`, `NEED_MORE_MEMORY`, `INTERVENE`, or
`CLOSE_PROCESS`, along with expansion depth and relevant probabilities when
available.

### Intervención

State clearly whether an intervention happened:

- If none happened, say so and report the highest recent `INTERVENE`
  probability and why it did not trigger when the reason is observable.
- If one happened, report its JEV probability/confidence, whether OpenRouter
  built it, whether OpenCode accepted it, and the prompt delivery status.
- Never claim the intervention text unless it is present in a log or durable
  event payload.

### Impacto

Determine whether the intervention affected the OpenCode agent by inspecting
events after the intervention. Cite observable evidence such as new reasoning,
tool calls, tool results, changed strategy, retry behavior, or task progress.
If the evidence is insufficient, say `impacto no confirmado` rather than
guessing.

### Agente OpenCode

Report whether Harbor, the trial container, and the OpenCode agent are
running. Check task artifacts such as `/app/gblock.txt`, relevant agent logs,
and the Harbor result when available.

### Tiempo

Report elapsed minutes from the actual trial/container start time. Prefer the
Harbor process elapsed time or container creation/start time. If the start
time cannot be determined, state that the elapsed time is unavailable. Do not
invent an ETA; only report remaining time if the effective task timeout and
start time are both known.

## Evidence Rules

- Query live process and container state before answering.
- Query SQLite for the relevant root session rather than aggregating unrelated
  historical roots.
- Read the latest Supervisor log lines and correlate event timestamps.
- Distinguish a JEV `400` from an EBI, OpenRouter, Harbor, or agent-tool error.
- Treat `HTTP 204` from OpenCode `prompt_async` as successful delivery, not as
  proof that the agent completed the requested action.
- Distinguish events received from events processed. Pending or processing
  events mean the cycle is not settled.
- If a query fails or data is stale, report the limitation explicitly.

## Minimal Commands

Use the repository's live paths and adapt the root session ID:

```bash
ps -p <supervisor_pid>,<harbor_pid> -o pid=,stat=,etime=,command=
docker ps --format '{{.Names}}\t{{.Status}}\t{{.State}}'
sqlite3 "$HOME/Library/Application Support/grams/supervisor.db" \
  "select status,count(*) from supervisor_events where root_session_id='<root>' group by status;"
```

Search recent Supervisor logs for `jev_supervision_decision`,
`model_call_failed`, `model_call_completed`, `BUILD_INTERVENTION`,
`prompt_async`, `events_acknowledged`, and `supervisor_cycle_failed`.

Keep status answers factual and compact. Do not start a new trial, cancel a
trial, modify code, or restart services in response to a status request unless
the user explicitly asks for that action.
