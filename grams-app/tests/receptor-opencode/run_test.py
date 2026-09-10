#!/usr/bin/env python3
"""Run the smallest Harbor/OpenCode receiver smoke test."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TEST_DIR = Path(__file__).resolve().parent
SERVER_DIR = ROOT / "grams-app" / "supervisor"
CONFIG = TEST_DIR / "test_job.json"
CACHE_SCRIPT = ROOT / "scripts" / "cache_opencode.sh"


def main() -> int:
    env = os.environ.copy()
    env.setdefault("GRAMS_EVENT_ENDPOINT", "http://host.docker.internal:8765/events")
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "supervisor.app:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8765",
            "--app-dir",
            str(SERVER_DIR),
        ],
        env=env,
    )
    try:
        time.sleep(0.5)
        cache = subprocess.run([str(CACHE_SCRIPT)], cwd=ROOT, env=env)
        if cache.returncode != 0:
            print("OpenCode cache preparation failed; Harbor should report infrastructure failure.", file=sys.stderr)
        command = [
            "docker",
            "compose",
            "exec",
            "-T",
            "grams-opencode",
            "harbor",
            "run",
            "--config",
            str(CONFIG),
            "--job-name",
            f"receptor-opencode-{int(time.time())}",
            "--yes",
            "--disable-verification",
            "--n-concurrent",
            "1",
        ]
        return subprocess.call(command, cwd=ROOT)
    finally:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
