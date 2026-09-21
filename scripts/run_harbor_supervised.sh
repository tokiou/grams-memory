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
config_path=""
port_overlay_present=0
while (($#)); do
  case "$1" in
    --config)
      if (($# < 2)); then
        printf '%s\n' '--config requires a value' >&2
        exit 2
      fi
      config_path="$2"
      harbor_args+=("$1" "$2")
      shift 2
      ;;
    --config=*)
      config_path="${1#*=}"
      harbor_args+=("$1")
      shift
      ;;
    --extra-docker-compose)
      if (($# < 2)); then
        printf '%s\n' '--extra-docker-compose requires a value' >&2
        exit 2
      fi
      if [[ "$2" == "$port_overlay" ]]; then
        port_overlay_present=1
      fi
      harbor_args+=("$1" "$2")
      shift 2
      ;;
    --extra-docker-compose=*)
      if [[ "${1#*=}" == "$port_overlay" ]]; then
        port_overlay_present=1
      fi
      harbor_args+=("$1")
      shift
      ;;
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

if [[ "$port_overlay_present" == "0" && -n "$config_path" && -f "$config_path" ]]; then
  if python3 - "$config_path" "$port_overlay" <<'PY'
import json
import sys
from pathlib import Path

config_path, overlay_path = sys.argv[1:]
try:
    config = json.loads(Path(config_path).read_text())
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)

configured = config.get("environment", {}).get("extra_docker_compose", [])
target = Path(overlay_path).resolve()
raise SystemExit(0 if any(Path(item).resolve() == target for item in configured) else 1)
PY
  then
    port_overlay_present=1
  fi
fi

if [[ "$port_overlay_present" == "0" ]]; then
  harbor_args+=(--extra-docker-compose "$port_overlay")
fi

exec docker compose exec -T grams-opencode harbor run \
  "${harbor_args[@]}" \
  --timeout-multiplier 1.0
