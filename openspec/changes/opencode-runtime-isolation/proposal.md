# Isolate OpenCode runtime storage and archive it after execution

## Objective

Prevent OpenCode's runtime files, including its SQLite database and transient
WAL/SHM files, from being written to `/logs/agent` while a trial is running.
The `GramsOpenCode` wrapper SHALL use container-local temporary XDG data and
state directories, then copy the complete runtime contents to
`/logs/agent/opencode` only after all OpenCode-related processes have stopped.
The health monitor SHALL have its Python runtime available, while existing
GRAMS event/intervention behavior and the wrapper's exit status remain
unchanged.

## Relevant Context

- `grams-opencode/grams_opencode.py` defines `GramsOpenCode`, generates the
  shell wrapper in `_opencode_wrapper`, and installs trial dependencies in
  `install`.
- The current wrapper starts `opencode-real serve`, waits on the local health
  endpoint, adds `--attach http://127.0.0.1:4096` to `run`, streams the real
  command through `tee`, preserves its result with `PIPESTATUS[0]`, keeps the
  server alive for the GRAMS follow-up grace period, and then terminates the
  server and health monitor.
- The current health monitor reads the hard-coded path
  `/logs/agent/opencode/xdg-data/opencode/log/opencode.log`. This allows
  OpenCode's XDG runtime data to be colocated with the bind-mounted/artifact
  path. Current trials have reported OpenCode SQLite corruption (`database
  disk image is malformed`).
- The current monitor is launched with `python3`, but `GramsOpenCode.install`
  installs only `curl`, `ca-certificates`, and `git`.
- Existing adapter tests are in
  `grams-app/tests/test_grams_opencode_adapter.py`. They inspect generated
  wrapper/install behavior and run the wrapper against a fake OpenCode binary.
- The plugin and Supervisor rely on `GRAMS_EVENT_ENDPOINT` and
  `GRAMS_INTERVENTION_ENDPOINT`; this change must not alter their contracts.

## Scope

### Included

- Isolating OpenCode `XDG_DATA_HOME` and `XDG_STATE_HOME` for each normal
  wrapper execution in container-local temporary storage.
- Making the health monitor follow the isolated runtime log.
- Copying the complete isolated data and state trees to
  `/logs/agent/opencode` after process shutdown.
- Installing `python3` for the existing monitor, or an equivalent runtime only
  if the monitor is changed consistently and is proven available.
- Unit/subprocess tests for runtime isolation, post-shutdown archiving,
  dependency installation, and exit-status preservation.

### Excluded

- Changes to the OpenCode plugin, event envelope, Supervisor Inbox, event
  endpoint, intervention endpoint, or intervention policy.
- Changes to OpenCode's SQLite implementation or attempts to repair an already
  corrupted database.
- Moving ordinary wrapper/server/run/health log artifacts out of
  `/logs/agent`; only OpenCode XDG data and state are in this isolation scope.
- Changes to OpenCode configuration discovery or plugin installation. The
  existing configuration/plugin location SHALL continue to work.

## Expected Behavior

For a normal invocation (anything other than the existing `--version` fast
path), the wrapper SHALL:

1. Create a unique runtime directory in container-local temporary storage,
   such as `/tmp`, with separate child directories for XDG data and XDG state.
2. Export those child directories as `XDG_DATA_HOME` and `XDG_STATE_HOME`
   before starting either the OpenCode server or the attached CLI process.
3. Pass the isolated runtime environment to both OpenCode processes and use the
   isolated data directory when locating the internal OpenCode log for the
   health monitor.
4. Preserve the current event monitor, intervention environment, server
   attachment, follow-up grace period, output streaming, and real-command exit
   status behavior.
5. Stop and wait for the OpenCode server and health monitor, and only then copy
   both runtime trees to `/logs/agent/opencode`.
6. Preserve the complete runtime contents, including nested files and SQLite
   companion files such as `*.db-wal` and `*.db-shm`, before removing the
   temporary runtime directory.

The existing `--version` path SHALL continue to execute the real binary
directly and return its status without starting the server or monitor.

## Functional Requirements

### Runtime isolation

1. The wrapper SHALL create a unique per-invocation temporary runtime root in
   storage local to the container and SHALL NOT derive that root from
   `/logs/agent`, `HOME`, inherited `XDG_DATA_HOME`, or inherited
   `XDG_STATE_HOME` values.
2. The wrapper SHALL set and export `XDG_DATA_HOME` and `XDG_STATE_HOME` to
   directories below that runtime root before launching `serve` and before
   launching the real attached CLI.
3. The wrapper SHALL override inherited XDG data/state values, including when
   an inherited value points into `/logs/agent`.
4. OpenCode runtime files written during execution SHALL resolve below the
   isolated XDG directories. The active internal log path supplied to the
   health monitor SHALL be derived from the isolated XDG data directory rather
   than hard-coded to the archive path.
5. The wrapper SHALL continue to use the existing configuration/plugin setup;
   isolating data and state SHALL not prevent the installed GRAMS plugin from
   loading or prevent the configured endpoints from being exported.

### Runtime archiving and cleanup

6. After the real CLI has completed and any configured follow-up grace period
   has elapsed, the wrapper SHALL stop the OpenCode server and SHALL wait until
   it has exited before archiving runtime data.
7. The wrapper SHALL stop and wait for the health monitor before archiving, so
   no monitor process can still be reading or observing the runtime log while
   the archive is copied.
8. The wrapper SHALL copy the complete XDG data tree to
   `/logs/agent/opencode/xdg-data` and the complete XDG state tree to
   `/logs/agent/opencode/xdg-state`. The copy SHALL include empty directories,
   hidden files, SQLite databases, WAL files, SHM files, logs, and nested
   files when present.
9. The archive operation SHALL be attempted on normal completion, real-command
   failure, server health-check failure, and wrapper cleanup paths after a
   runtime root has been created.
10. The temporary runtime tree SHALL not be left as the active OpenCode data or
    state location after the wrapper exits. Cleanup SHALL occur only after the
    archive attempt.
11. The wrapper SHALL retain the existing `/logs/agent` locations for
    `opencode-server.log`, `opencode-wrapper.log`, `opencode-run.jsonl`, and
    `opencode-health.log` unless a change is required solely to implement the
    runtime isolation.

### Health monitor and installation

12. The existing Python health-monitor implementation SHALL continue to be
    used by default, and the trial installation SHALL make `python3` available
    before the wrapper can invoke it. The preferred installation change is to
    add the `python3` package to the existing apt installation while retaining
    `curl`, `ca-certificates`, and `git`.
13. An alternative interpreter is acceptable only if the wrapper invokes that
    interpreter instead of `python3` and tests verify that it is available in a
    freshly installed trial environment and that it preserves the monitor's
    current behavior.
14. The monitor SHALL continue to emit the same
    `SESSION_INTERNAL_ERROR` payload, endpoint request, session extraction,
    fingerprint de-duplication, and one-second polling behavior. Only its log
    input path may change.

### Compatibility and status behavior

15. `GRAMS_EVENT_ENDPOINT` and `GRAMS_INTERVENTION_ENDPOINT` SHALL retain
    their current values, export behavior, and empty/unset handling.
16. The wrapper SHALL continue to attach `run` invocations to the local
    server, preserve the follow-up grace period, and leave the existing
    OpenCode/plugin event and intervention flow unchanged.
17. For a real CLI invocation, the wrapper's exit status SHALL remain the
    status captured from the real OpenCode command (`PIPESTATUS[0]`), including
    non-zero statuses. Starting, stopping, archiving, or deleting temporary
    runtime data SHALL not replace that status on the normal command path.
18. The existing `--version` command and its exit status SHALL remain
    unchanged.

## Non-Functional Requirements

- Runtime SQLite files SHALL not be actively opened or updated under the
  bind-mounted `/logs/agent` path during OpenCode execution.
- The temporary runtime directory SHALL be unique per wrapper invocation so
  concurrent or repeated invocations cannot share OpenCode databases.
- Archiving SHALL finish before the wrapper exits, and the archive SHALL be
  usable for post-trial diagnosis without requiring access to the temporary
  path.
- The change SHALL not add a second event queue, alter event delivery timing,
  or expose credentials in newly added logs or tests.

## Affected Components

- `grams-opencode/grams_opencode.py`:
  generated wrapper lifecycle, internal-log path, cleanup/archive logic, and
  `GramsOpenCode.install` dependency command.
- `grams-app/tests/test_grams_opencode_adapter.py`:
  install assertions and wrapper subprocess/regression tests.
- No production changes are required in the OpenCode plugin or Supervisor
  components.

## Constraints

- The implementation SHALL use the existing `GramsOpenCode` adapter and
  generated wrapper rather than introducing a separate launcher.
- `/logs/agent` remains the artifact/bind-mounted output location and must be
  available for wrapper logs and the final archive.
- The server must continue to listen on port `4096`, and the wrapper must
  continue to use the existing local health endpoint and attach URL.
- The existing GRAMS endpoint environment variables and follow-up grace
  semantics must remain compatible with current trials.
- Only specification files may be changed for this request; implementation
  work is covered by this specification and must not be performed as part of
  authoring it.

## Edge Cases

- The container starts with `XDG_DATA_HOME` or `XDG_STATE_HOME` already set to
  `/logs/agent` or another persistent path; both values must be overridden.
- OpenCode creates a database, WAL, SHM file, or log only near shutdown; the
  final archive must include it.
- The server fails its health check before the monitor starts; the wrapper must
  still preserve its startup-failure status and attempt archive cleanup.
- The real CLI exits non-zero; the archive must still be attempted and the
  same CLI status must be returned.
- The OpenCode run is aborted or interrupted while the server and monitor are
  active; cleanup must stop/wait for child processes before copying.
- The runtime data or state directory is empty or is not created by OpenCode;
  archiving must still complete without treating the absence of optional files
  as a successful-run failure.
- `/logs/agent/opencode` already exists; the wrapper must create required child
  directories and update the current invocation's files without failing solely
  because the destination exists.
- The wrapper is invoked with `--version`; it must retain the current direct
  execution path and must not require a server or health monitor.

## Error Handling

- Failure to install the required monitor runtime SHALL fail installation in
  the existing way; a trial must not claim successful setup while `python3`
  is unavailable.
- A health-monitor failure SHALL be logged as monitor failure and SHALL NOT
  change the real OpenCode command's exit status, matching the current
  best-effort monitor design.
- A runtime archive/copy failure SHALL be reported in the wrapper log and
  SHALL not mask the already captured real-command status. The wrapper SHALL
  still attempt to stop children and remove the temporary runtime directory.
- A server readiness failure SHALL retain the current non-zero startup failure
  behavior while still performing best-effort shutdown and archival.
- Existing GRAMS event/intervention request failures SHALL retain their current
  best-effort behavior; runtime archival must not emit duplicate or synthetic
  GRAMS events.

## Acceptance Criteria

1. A generated wrapper run with inherited XDG paths pointing at
   `/logs/agent` records container-local, per-run values for both
   `XDG_DATA_HOME` and `XDG_STATE_HOME` in the fake OpenCode process; neither
   active value is under `/logs/agent`.
2. After a successful fake run, files written under both isolated XDG trees,
   including representative database, WAL/SHM, log, hidden, and nested files,
   exist under `/logs/agent/opencode/xdg-data` or
   `/logs/agent/opencode/xdg-state` after the wrapper exits.
3. The archive is produced only after the fake server and health monitor have
   stopped, and the temporary runtime directory is no longer the active
   runtime location.
4. A fake real OpenCode command returning a non-zero status causes the wrapper
   to return that exact status while still attempting and producing the
   runtime archive.
5. A generated wrapper run retains the existing attach behavior, follow-up
   grace behavior, event endpoint handling, intervention endpoint handling,
   and server/monitor cleanup behavior.
6. The install command retains `curl`, `ca-certificates`, and `git` and also
   makes `python3` available (or uses a tested equivalent consistently in the
   wrapper); the existing health monitor no longer fails solely because its
   interpreter is missing.
7. Existing adapter tests continue to pass, with assertions updated for the
   dependency and runtime-path changes, and new isolation/archive/status tests
   pass.
8. `git diff --check` passes for the implementation and test changes.

## Test Scenarios

1. **Install dependency contract:** inspect or exercise the install command and
   assert that the existing packages plus `python3` are installed, and that a
   fresh target environment can execute `python3`.
2. **XDG override:** run the generated wrapper with inherited XDG variables
   pointing into an artifact directory and a fake OpenCode binary that records
   its environment. Assert that both server and CLI see the same per-run,
   container-local XDG directories.
3. **Complete archive:** have the fake server/CLI write representative files
   to both XDG directories, including `opencode.db`, `opencode.db-wal`,
   `opencode.db-shm`, a log, a hidden file, and a nested state file. Assert
   that all are present in the corresponding final archive after exit.
4. **Shutdown ordering:** have the fake server write a final marker while
   handling termination and assert that the marker is present in the archive;
   assert that the health monitor has also exited before the archive check.
5. **Non-zero status:** make the fake CLI exit with a known non-zero status and
   assert that the wrapper returns the same status and still archives runtime
   data.
6. **Readiness failure:** make the health endpoint fail and assert that the
   wrapper retains its existing startup-failure status and does not leave a
   running server or temporary runtime process.
7. **GRAMS regressions:** retain/update the existing assertions for event and
   intervention endpoint configuration, attached execution, follow-up grace,
   and supervisor-abort handling; verify that no new runtime-storage behavior
   changes those paths.
8. **Version fast path:** invoke `--version` with a fake real binary and assert
   that it is called directly, no server/archive monitor is started, and its
   status/output are preserved.

## Out of Scope

- Migrating or repairing databases created by earlier corrupted trials.
- Changing where Harbor or the Docker Compose service mounts artifact logs.
- Changing event schemas, plugin hooks, intervention delivery, Supervisor
  processing, or OpenCode version pinning.
- Adding a persistent database synchronization or backup service.

## Assumptions

- `/tmp` (or the selected equivalent) is container-local and is not a
  bind-mounted artifact path in the target trial environment.
- `/logs/agent/opencode` is the stable post-run diagnostic destination, and
  preserving the XDG-relative `xdg-data`/`xdg-state` layout is compatible with
  existing artifact inspection (including the current internal-log layout).
- The target trial image can install Debian's `python3` package through the
  existing apt-based setup. If that is not true for a supported image, the
  implementation must use and test an equivalent interpreter as described in
  the functional requirements.
- The existing follow-up grace period is intentionally required so Supervisor
  interventions can arrive before the server is stopped; it must occur before
  the final archive.
