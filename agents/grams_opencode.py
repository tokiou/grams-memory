from harbor.agents.installed.opencode import OpenCode
from harbor.environments.base import BaseEnvironment


class GramsOpenCode(OpenCode):
    """OpenCode adapter for GRAMS experiments."""

    OPENCODE_VERSION = "1.18.22"

    @staticmethod
    def name() -> str:
        return "grams-opencode"

    async def install(self, environment: BaseEnvironment) -> None:
        # Dependencies required by the official OpenCode installer.
        await self.exec_as_root(
            environment,
            command="apt-get update && apt-get install -y curl ca-certificates",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )

        # Use the official installer instead of npm.
        # npm installation currently fails in some TB2 x64/glibc environments.
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                'export OPENCODE_INSTALL_DIR="$HOME/.opencode/bin"; '
                "curl -fsSL https://opencode.ai/install "
                f"| bash -s -- --version {self.OPENCODE_VERSION} --no-modify-path; "
                'mkdir -p "$HOME/.nvm"; '
                'printf \'export PATH="$HOME/.opencode/bin:$PATH"\\n\' '
                '> "$HOME/.nvm/nvm.sh"; '
                '. "$HOME/.nvm/nvm.sh"; '
                "opencode --version"
            ),
        )

    def get_version_command(self) -> str:
        return (
            'export PATH="$HOME/.opencode/bin:$PATH"; '
            "opencode --version"
        )