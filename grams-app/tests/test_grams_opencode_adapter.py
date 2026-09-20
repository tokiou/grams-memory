import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "grams-opencode"))

from grams_opencode import GramsOpenCode


def test_supervisor_abort_is_not_classified_as_agent_failure():
    agent = object.__new__(GramsOpenCode)
    agent._parse_stdout = lambda: [
        {"type": "error", "error": {"data": {"message": "Aborted"}}},
        {"type": "error", "error": {"data": {"message": "real failure"}}},
    ]

    assert agent._error_messages() == ["real failure"]


def test_aborted_wrapper_keeps_server_for_follow_up_prompt():
    wrapper = GramsOpenCode._opencode_wrapper()

    assert "run_output=/logs/agent/opencode-run.jsonl" in wrapper
    assert '"$real" "${args[@]}" 2>&1 | tee "$run_output"' in wrapper
    assert 'status=${PIPESTATUS[0]}' in wrapper
    assert 'GRAMS_EVENT_ENDPOINT:-' in wrapper
    assert "GRAMS_FOLLOW_UP_GRACE_SECONDS" in wrapper
    assert "follow-up prompt" in wrapper


def test_aborted_wrapper_keeps_fake_server_alive(tmp_path):
    home = tmp_path / "home"
    real_path = home / ".opencode" / "bin" / "opencode-real"
    real_path.parent.mkdir(parents=True)
    real_path.write_text(
        """#!/usr/bin/env bash
set -u
if [[ "${1:-}" == "serve" ]]; then
  echo "$$" > "$HOME/server.pid"
  touch "$HOME/server.alive"
  trap 'rm -f "$HOME/server.alive" "$HOME/server.pid"; exit 0' TERM INT EXIT
  while true; do sleep 1; done
fi
printf '%s\\n' '{"type":"error","error":{"data":{"message":"Aborted"}}}'
"""
    )
    real_path.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "curl").chmod(0o755)
    log_dir = tmp_path / "logs" / "agent"
    log_dir.mkdir(parents=True)
    wrapper = GramsOpenCode._opencode_wrapper().replace("/logs/agent", str(log_dir))
    wrapper_path = tmp_path / "opencode"
    wrapper_path.write_text(wrapper)
    wrapper_path.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "GRAMS_EVENT_ENDPOINT": "http://supervisor/events",
    }
    process = subprocess.Popen(
        [str(wrapper_path), "run", "--model=test/model"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(40):
        if (home / "server.alive").exists():
            break
        time.sleep(0.05)

    assert process.poll() is None
    assert (home / "server.alive").exists()
    server_pid = int((home / "server.pid").read_text())
    process.terminate()
    process.wait(timeout=5)
    os.kill(server_pid, signal.SIGTERM)
    for _ in range(20):
        if not (home / "server.alive").exists():
            break
        time.sleep(0.05)
    assert not (home / "server.alive").exists()
