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
SERVER = ROOT / "grams-app" / "supervisor" / "event_server.py"
CONFIG = TEST_DIR / "test_job.json"


def main() -> int:
    env = os.environ.copy()
    env.setdefault("GRAMS_EVENT_ENDPOINT", "http://host.docker.internal:8765/events")
    server = subprocess.Popen([sys.executable, str(SERVER)], env=env)
    try:
        time.sleep(0.5)
        command = [
            "docker",
            "compose",
            "exec",
            "-T",
            "harbor",
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
