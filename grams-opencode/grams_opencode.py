import base64
import os
import shlex
from pathlib import Path

from harbor.agents.installed.base import ErrorPattern, NonZeroAgentExitCodeError
from harbor.agents.installed.opencode import OpenCode
from harbor.environments.base import BaseEnvironment


class OpenCodeInfrastructureError(NonZeroAgentExitCodeError):
    """The pinned OpenCode artifact was not available in the trial cache."""


class GramsOpenCode(OpenCode):
    """OpenCode adapter using a host-prepared, pinned binary artifact."""

    OPENCODE_VERSION = "1.18.22"
    EVENT_ENDPOINT = "http://host.docker.internal:8765/events"
    CACHE_PATH = "/opt/grams-opencode-cache/opencode"
    ERROR_PATTERNS = [
        ErrorPattern(r"GRAMS_OPENCODE_CACHE_MISSING|GRAMS_OPENCODE_VERSION_MISMATCH", OpenCodeInfrastructureError),
        *OpenCode.ERROR_PATTERNS,
    ]

    @staticmethod
    def name() -> str:
        return "grams-opencode"

    def _error_messages(self) -> list[str]:
        # A supervisor abort is an intentional control action, not an agent failure.
        return [message for message in super()._error_messages() if message.strip().casefold() != "aborted"]

    @staticmethod
    def _plugin_source() -> str:
        return (
            Path(__file__).resolve().parent
            / "opencode_plugin"
            / "src"
            / "index.ts"
        ).read_text()

    @staticmethod
    def _opencode_wrapper() -> str:
        return r'''#!/usr/bin/env bash
set -u

real="$HOME/.opencode/bin/opencode-real"
server_log=/logs/agent/opencode-server.log
wrapper_log=/logs/agent/opencode-wrapper.log
run_output=/logs/agent/opencode-run.jsonl
mkdir -p /logs/agent
if [[ "${1:-}" == "--version" ]]; then
  exec "$real" "$@"
fi

printf 'Starting OpenCode server on port 4096\\n' >"$wrapper_log"
"$real" serve --hostname 0.0.0.0 --port 4096 >"$server_log" 2>&1 &
server_pid=$!
ready=0
for _ in $(seq 1 120); do
  if curl --connect-timeout 1 --max-time 2 -fsS \
      http://127.0.0.1:4096/global/health >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.1
done
if [[ "$ready" != "1" ]]; then
  printf 'OpenCode server failed health check after 12 seconds\\n' >>"$wrapper_log"
  curl --max-time 2 -v http://127.0.0.1:4096/global/health >>"$wrapper_log" 2>&1 || true
  kill "$server_pid" 2>/dev/null || true
  exit 1
fi
printf 'OpenCode server health check passed\\n' >>"$wrapper_log"

args=()
attached=0
for arg in "$@"; do
  args+=("$arg")
  if [[ "$arg" == "run" && "$attached" == "0" ]]; then
    args+=(--attach http://127.0.0.1:4096)
    attached=1
  fi
done

trap 'kill "$server_pid" 2>/dev/null || true' EXIT
"$real" "${args[@]}" 2>&1 | tee "$run_output"
status=${PIPESTATUS[0]}
if [[ -n "${GRAMS_EVENT_ENDPOINT:-}" ]]; then
  # The plugin can receive session.error after the CLI stream has ended. Keep
  # the API alive long enough for the Supervisor's follow-up prompt to arrive.
  sleep "${GRAMS_FOLLOW_UP_GRACE_SECONDS:-90}"
fi
kill "$server_pid" 2>/dev/null || true
trap - EXIT
exit "$status"
'''

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command="apt-get update && apt-get install -y curl ca-certificates",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )

        wrapper = base64.b64encode(self._opencode_wrapper().encode()).decode()
        endpoint = os.environ.get("GRAMS_EVENT_ENDPOINT", self.EVENT_ENDPOINT)
        path_line = 'export PATH="$HOME/.opencode/bin:$PATH"'
        endpoint_line = f"export GRAMS_EVENT_ENDPOINT={endpoint}"
        plugin = base64.b64encode(self._plugin_source().encode()).decode()
        command = (
            "set -euo pipefail; "
            'mkdir -p "$HOME/.opencode/bin"; '
            f'if [[ ! -x "{self.CACHE_PATH}" ]]; then '
            'printf \'GRAMS_OPENCODE_CACHE_MISSING\\n\' >&2; exit 78; fi; '
            f'cp "{self.CACHE_PATH}" "$HOME/.opencode/bin/opencode-real"; '
            f'if ! "$HOME/.opencode/bin/opencode-real" --version | grep -Fq {shlex.quote(self.OPENCODE_VERSION)}; then '
            'printf \'GRAMS_OPENCODE_VERSION_MISMATCH\\n\' >&2; exit 78; fi; '
            f"printf '%s' {shlex.quote(wrapper)} | base64 -d > "
            '"$HOME/.opencode/bin/opencode"; '
            'chmod 0755 "$HOME/.opencode/bin/opencode"; '
            'mkdir -p "$HOME/.nvm"; '
            'if [[ "${GRAMS_MODE:-on}" != "off" ]]; then '
            'mkdir -p "$HOME/.config/opencode/plugins"; '
            f"printf '%s' {shlex.quote(plugin)} | base64 -d > "
            '"$HOME/.config/opencode/plugins/grams-receiver.ts"; '
            'fi; '
            f"printf '%s\\n' {shlex.quote(path_line)}"
            ' > "$HOME/.nvm/nvm.sh"; '
            'if [[ "${GRAMS_MODE:-on}" != "off" ]]; then '
            f"printf '%s\\n' {shlex.quote(endpoint_line)}"
            ' >> "$HOME/.nvm/nvm.sh"; '
            'fi; '
            '. "$HOME/.nvm/nvm.sh"; '
            "opencode --version"
        )
        await self.exec_as_agent(environment, command=command)

    def get_version_command(self) -> str:
        return (
            'export PATH="$HOME/.opencode/bin:$PATH"; '
            "opencode --version"
        )
