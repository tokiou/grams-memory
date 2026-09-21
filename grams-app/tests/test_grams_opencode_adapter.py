import os
import inspect
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


def test_install_includes_git_for_opencode_snapshot_vcs():
    source = inspect.getsource(GramsOpenCode.install)

    assert "apt-get install -y curl ca-certificates git python3" in source


def test_aborted_wrapper_keeps_server_for_follow_up_prompt():
    wrapper = GramsOpenCode._opencode_wrapper()

    assert "run_output=/logs/agent/opencode-run.jsonl" in wrapper
    assert 'export XDG_DATA_HOME="$data_home"' in wrapper
    assert 'export XDG_STATE_HOME="$state_home"' in wrapper
    assert 'cp -a "$data_home"/. "$archive_data"/' in wrapper
    assert 'cp -a "$state_home"/. "$archive_state"/' in wrapper
    assert 'internal_log="$data_home/opencode/log/opencode.log"' in wrapper
    assert 'mkfifo "$run_pipe"' in wrapper
    assert 'tee "$run_output" <"$run_pipe" &' in wrapper
    assert '"$real" "${args[@]}" >"$run_pipe" 2>&1 &' in wrapper
    assert 'status=$?' in wrapper
    assert 'GRAMS_EVENT_ENDPOINT:-' in wrapper
    assert "GRAMS_FOLLOW_UP_GRACE_SECONDS" in wrapper
    assert "follow-up prompt" in wrapper


def test_install_configures_pending_intervention_endpoint():
    source = inspect.getsource(GramsOpenCode.install)

    assert "GRAMS_INTERVENTION_ENDPOINT" in source
    assert "INTERVENTION_ENDPOINT" in source


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
    try:
        os.kill(server_pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(20):
        if not (home / "server.alive").exists():
            break
        time.sleep(0.05)
    assert not (home / "server.alive").exists()


def test_wrapper_isolates_runtime_and_archives_after_nonzero_run(tmp_path):
    home = tmp_path / "home"
    real_path = home / ".opencode" / "bin" / "opencode-real"
    real_path.parent.mkdir(parents=True)
    real_path.write_text(
        """#!/usr/bin/env bash
set -u
if [[ "${1:-}" == "serve" ]]; then
  mkdir -p "$XDG_DATA_HOME/opencode/log" "$XDG_STATE_HOME/opencode"
  printf '%s\\n' "$XDG_DATA_HOME" > "$HOME/data-home"
  printf '%s\\n' "$XDG_STATE_HOME" > "$HOME/state-home"
  printf 'runtime log\\n' > "$XDG_DATA_HOME/opencode/log/opencode.log"
  printf 'wal\\n' > "$XDG_DATA_HOME/opencode/opencode.db-wal"
  printf 'shm\\n' > "$XDG_DATA_HOME/opencode/opencode.db-shm"
  echo "$$" > "$HOME/server.pid"
  touch "$HOME/server.alive"
  trap 'rm -f "$HOME/server.alive" "$HOME/server.pid"; exit 0' TERM INT EXIT
  while true; do sleep 1; done
fi
exit 7
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
        "GRAMS_EVENT_ENDPOINT": "",
        "GRAMS_FOLLOW_UP_GRACE_SECONDS": "0",
        "XDG_DATA_HOME": str(log_dir / "inherited-data"),
        "XDG_STATE_HOME": str(log_dir / "inherited-state"),
    }
    process = subprocess.run(
        [str(wrapper_path), "run", "--model=test/model"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert process.returncode == 7
    data_home = Path((home / "data-home").read_text().strip())
    state_home = Path((home / "state-home").read_text().strip())
    assert data_home != log_dir / "inherited-data"
    assert state_home != log_dir / "inherited-state"
    assert not data_home.exists()
    assert not state_home.exists()
    assert (log_dir / "opencode" / "xdg-data" / "opencode" / "log" / "opencode.log").exists()
    assert (log_dir / "opencode" / "xdg-data" / "opencode" / "opencode.db-wal").exists()
    assert (log_dir / "opencode" / "xdg-data" / "opencode" / "opencode.db-shm").exists()
    assert (log_dir / "opencode" / "xdg-state" / "opencode").is_dir()
