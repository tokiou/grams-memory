import base64
import os
import shlex
from pathlib import Path

from harbor.agents.installed.opencode import OpenCode
from harbor.environments.base import BaseEnvironment


class GramsOpenCode(OpenCode):
    """OpenCode adapter that installs the GRAMS receiver plugin in each trial."""

    OPENCODE_VERSION = "1.18.22"
    EVENT_ENDPOINT = "http://host.docker.internal:8765/events"

    @staticmethod
    def name() -> str:
        return "grams-opencode"

    @staticmethod
    def _plugin_source() -> str:
        return (
            Path(__file__).resolve().parent
            / "opencode_plugin"
            / "src"
            / "index.ts"
        ).read_text()

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command="apt-get update && apt-get install -y curl ca-certificates",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )

        plugin = base64.b64encode(self._plugin_source().encode()).decode()
        endpoint = os.environ.get("GRAMS_EVENT_ENDPOINT", self.EVENT_ENDPOINT)
        path_line = 'export PATH="$HOME/.opencode/bin:$PATH"'
        endpoint_line = f"export GRAMS_EVENT_ENDPOINT={endpoint}"
        command = (
            "set -euo pipefail; "
            'export OPENCODE_INSTALL_DIR="$HOME/.opencode/bin"; '
            "curl -fsSL https://opencode.ai/install "
            f"| bash -s -- --version {self.OPENCODE_VERSION} --no-modify-path; "
            'mkdir -p "$HOME/.config/opencode/plugins" "$HOME/.nvm"; '
            f"printf '%s' {shlex.quote(plugin)} | base64 -d > "
            '"$HOME/.config/opencode/plugins/grams-receiver.ts"; '
            f"printf '%s\\n%s\\n' {shlex.quote(path_line)} "
            f"{shlex.quote(endpoint_line)}"
            ' > "$HOME/.nvm/nvm.sh"; '
            '. "$HOME/.nvm/nvm.sh"; '
            "opencode --version"
        )
        await self.exec_as_agent(environment, command=command)

    def get_version_command(self) -> str:
        return (
            'export PATH="$HOME/.opencode/bin:$PATH"; '
            "opencode --version"
        )
