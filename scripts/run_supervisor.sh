#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "$repo_root/.env" ]]; then
  set -a
  # Keep local service credentials in the repository's existing environment file.
  # shellcheck disable=SC1091
  source "$repo_root/.env"
  set +a
fi

export GRAMS_SUPERVISOR_WORKER_ENABLED="${GRAMS_SUPERVISOR_WORKER_ENABLED:-true}"

exec "$repo_root/.venv/bin/uvicorn" supervisor.app:app \
  --app-dir "$repo_root/grams-app" \
  --host "${GRAMS_SUPERVISOR_HOST:-0.0.0.0}" \
  --port "${GRAMS_SUPERVISOR_PORT:-8765}" \
  "$@"
