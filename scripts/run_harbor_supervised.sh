#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
port_overlay="$repo_root/grams-app/tests/receptor-opencode/docker-compose-opencode-port.yml"
export GRAMS_REPO_ROOT="$repo_root"

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

harbor_args=()
while (($#)); do
  case "$1" in
    --timeout-multiplier)
      if (($# < 2)); then
        printf '%s\n' '--timeout-multiplier requires a value' >&2
        exit 2
      fi
      shift 2
      ;;
    --timeout-multiplier=*)
      shift
      ;;
    *)
      harbor_args+=("$1")
      shift
      ;;
  esac
done

exec docker compose exec -T grams-opencode harbor run \
  "${harbor_args[@]}" \
  --timeout-multiplier 1.0 \
  --extra-docker-compose "$port_overlay"
