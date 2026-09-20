---
name: run-harbor-trial
description: Use when the user asks to launch, run, start, or execute a supervised Harbor trial. Always run it in the background with timeout multiplier 1.0 so status can be queried in the same chat.
---

# Run Harbor Trial

Use this skill whenever the user asks to launch a Harbor trial in this
repository.

## Required Launch Policy

- Use `scripts/run_harbor_supervised.sh` from the repository root.
- Always use timeout multiplier `1.0`. Do not honor a different multiplier
  from the environment or user-provided Harbor arguments.
- Run exactly one trial at a time because host port `4096` is fixed.
- Launch in the background and return control to the chat after verifying that
  the process did not fail immediately.
- Redirect combined stdout and stderr to a unique local log under `/tmp`.
- Retain and report the background PID, log path, job name, config path, start
  time, and expected `root_session_id` when it becomes available.
- Never print credentials or `.env` contents.

## Before Launch

Check for an existing `harbor run` process and a running trial container that
uses port `4096`. If either exists, do not launch a second trial; report the
conflict and offer to inspect its status.

Validate that the requested Harbor config exists. Use the repository wrapper,
which pins the effective multiplier to `1.0` even if a caller passes
`--timeout-multiplier` or sets `HARBOR_TIMEOUT_MULTIPLIER`.

## Background Command

Choose a unique log path such as:

```text
/tmp/grams-harbor-<job-name>-<UTC timestamp>.log
```

Launch the wrapper with `nohup`, redirect both output streams, and background
the process. The effective command must be equivalent to:

```bash
HARBOR_TIMEOUT_MULTIPLIER=1.0 nohup ./scripts/run_harbor_supervised.sh \
  --config "<config>" \
  --job-name "<job-name>" \
  --yes --disable-verification --n-concurrent 1 \
  > "<log-path>" 2>&1 &
```

Shell-quote every dynamic value, reject control characters, and use an argument
array when constructing the command programmatically. Never concatenate raw
user input into shell syntax.

Do not wait for Harbor to finish. After launch, verify the PID with `ps` and
inspect the log for an immediate startup error. Report the effective multiplier
as `1.0`.

## Later Status Requests

For a later `status` request in the same chat, reuse the PID, log path, job
name, and root session from the launch. Load and follow the
`status-supervisor` skill. Query live processes, Docker, the relevant Inbox
root, recent Supervisor decisions, and Harbor result artifacts. Never launch a
new trial in response to a status request.

If the background process has exited, inspect its log and Harbor `result.json`
before classifying the outcome. A recorded PID alone is not proof that the
trial is still active or succeeded.
