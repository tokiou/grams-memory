#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
port_overlay="$repo_root/grams-app/tests/receptor-opencode/docker-compose-opencode-port.yml"

# Prepare the pinned artifact once on the host. If the network is unavailable,
# still start Harbor so the trial records a typed infrastructure error instead
# of being reported as an action-agent failure.
set +e
"$repo_root/scripts/cache_opencode.sh"
cache_status=$?
set -e
if [[ "$cache_status" != "0" ]]; then
  printf 'OpenCode cache preparation failed; trial will be classified as infrastructure error.\n' >&2
fi

exec docker compose exec -T grams-opencode harbor run \
  "$@" \
  --extra-docker-compose "$port_overlay"
