import logging
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "supervisor"))

from supervisor.config import Config
from supervisor.observability import (
    ColoredFormatter,
    begin_node,
    bind_correlation,
    emit,
    finish_node,
    graph_trace,
    monotonic_ns,
    parse_source_timestamp,
    sanitize,
    source_lag_info,
    source_lag_ms,
    trace_summary,
)


def test_colored_formatter_colors_level_and_hides_sensitive_fields():
    formatter = ColoredFormatter(color=True)
    record = logging.getLogger("test").makeRecord(
        "test", logging.ERROR, __file__, 1, "external_call_failed", (), None,
        extra={"event_name": "external_call_failed", "service": "model", "payload": {"secret": "no"}},
    )

    rendered = formatter.format(record)

    assert "\x1b[31mERROR" in rendered
    assert "external_call_failed" in rendered
    assert "payload" not in rendered
    assert "secret" not in rendered


def test_colored_formatter_can_be_disabled():
    formatter = ColoredFormatter(color=False)
    record = logging.getLogger("test").makeRecord(
        "test", logging.INFO, __file__, 1, "event_persisted", (), None,
        extra={"event_name": "event_persisted"},
    )

    assert "\x1b[" not in formatter.format(record)


def test_source_lag_uses_normalized_timestamp_and_rejects_future_clock_skew():
    now = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
    payload = {"timestamp": "2026-01-01T00:00:00Z"}

    assert parse_source_timestamp(payload) == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert source_lag_ms(payload, now=now) == 1000.0
    assert source_lag_ms({"timestamp": "2026-01-01T00:00:02Z"}, now=now) is None
    assert source_lag_info({}, now=now) == (None, "missing")
    assert source_lag_info({"timestamp": "not-a-date"}, now=now) == (None, "invalid")


def test_config_validates_logging_options(tmp_path):
    with pytest.raises(ValueError, match="log_color"):
        Config(tmp_path / "supervisor.db", log_color="rainbow")
    with pytest.raises(ValueError, match="log_lag_warn_ms"):
        Config(tmp_path / "supervisor.db", log_lag_warn_ms=-1)


def test_emit_propagates_correlation_without_logging_payload(caplog):
    logger = logging.getLogger("supervisor.test")
    with caplog.at_level(logging.INFO):
        with bind_correlation(run_id="run-1", root_session_id="root"):
            emit(logger, logging.INFO, "run_completed", payload={"api_key": "secret"}, outcome="ok")

    record = caplog.records[-1]
    assert record.event_name == "run_completed"
    assert record.run_id == "run-1"
    assert record.root_session_id == "root"
    assert not hasattr(record, "payload")


def test_sanitize_redacts_common_secret_formats():
    rendered = sanitize('Authorization: Bearer abc123 api_key="def456" password=ghi789')

    assert "abc123" not in rendered
    assert "def456" not in rendered
    assert "ghi789" not in rendered


def test_graph_trace_counts_steps_and_previous_node():
    with graph_trace():
        first, previous, gap = begin_node("REVIEW")
        finish_node("REVIEW", monotonic_ns())
        second, previous, gap = begin_node("MEMORY_OPERATION")

        summary = trace_summary()

    assert first == 1
    assert previous == "REVIEW"
    assert second == 2
    assert gap is not None
    assert summary == {"steps": 2, "node_counts": {"REVIEW": 1, "MEMORY_OPERATION": 1}}
