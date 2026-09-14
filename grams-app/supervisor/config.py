"""Configuration for the GRAMS event receiver."""

from dataclasses import dataclass
import math
import os
from pathlib import Path


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _log_color(name: str, default: str) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in {"auto", "always", "never"}:
        raise ValueError(f"{name} must be auto, always, or never")
    return value


@dataclass(frozen=True)
class Config:
    """Validated settings for event ingress and Inbox persistence."""

    db_path: Path
    host: str = "0.0.0.0"
    port: int = 8765
    inbox_batch_size: int = 20
    inbox_max_pending: int = 10_000
    processing_lease_seconds: float = 60.0
    max_attempts: int = 3
    log_level: str = "INFO"
    log_color: str = "auto"
    log_lag_warn_ms: float | None = None
    log_file: Path | None = None

    def __post_init__(self) -> None:
        if not str(self.db_path).strip():
            raise ValueError("db_path must not be empty")
        if not self.host.strip() or self.port <= 0 or self.port > 65535:
            raise ValueError("host and port must be valid")
        if self.inbox_batch_size <= 0 or self.inbox_max_pending <= 0:
            raise ValueError("inbox limits must be positive")
        if self.processing_lease_seconds <= 0 or not math.isfinite(self.processing_lease_seconds):
            raise ValueError("processing lease must be positive and finite")
        if self.max_attempts <= 0:
            raise ValueError("max attempts must be positive")
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level must be a valid Python logging level")
        if self.log_color not in {"auto", "always", "never"}:
            raise ValueError("log_color must be auto, always, or never")
        if self.log_lag_warn_ms is not None and (
                not math.isfinite(self.log_lag_warn_ms) or self.log_lag_warn_ms < 0):
            raise ValueError("log_lag_warn_ms must be finite and non-negative")

    @classmethod
    def from_env(cls) -> "Config":
        default_db = Path.home() / "Library" / "Application Support" / "grams" / "supervisor.db"
        raw_db = os.getenv("GRAMS_SUPERVISOR_DB_PATH", os.getenv("GRAMS_DB_PATH", str(default_db)))
        if not raw_db.strip():
            raise ValueError("database path must not be empty")
        return cls(
            db_path=Path(raw_db).expanduser(),
            host=os.getenv("GRAMS_SUPERVISOR_HOST", "0.0.0.0"),
            port=_positive_int("GRAMS_SUPERVISOR_PORT", 8765),
            inbox_batch_size=_positive_int("GRAMS_INBOX_BATCH_SIZE", 20),
            inbox_max_pending=_positive_int("GRAMS_INBOX_MAX_PENDING", 10_000),
            processing_lease_seconds=_positive_float("GRAMS_PROCESSING_LEASE_SECONDS", 60.0),
            max_attempts=_positive_int("GRAMS_MAX_ATTEMPTS", 3),
            log_level=os.getenv("GRAMS_LOG_LEVEL", "INFO"),
            log_color=_log_color("GRAMS_LOG_COLOR", "auto"),
            log_lag_warn_ms=(
                float(os.environ["GRAMS_LOG_LAG_WARN_MS"])
                if os.getenv("GRAMS_LOG_LAG_WARN_MS") else None
            ),
            log_file=Path(os.environ["GRAMS_LOG_FILE"]).expanduser()
            if os.getenv("GRAMS_LOG_FILE") else None,
        )
