"""Configuration for the Supervisor composition root."""

from dataclasses import dataclass
import os
import math
from pathlib import Path
from urllib.parse import urlparse


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


def _url(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be an absolute HTTP(S) URL")
    return value.rstrip("/")


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _log_color(name: str, default: str) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in {"auto", "always", "never"}:
        raise ValueError(f"{name} must be auto, always, or never")
    return value


def _optional_nonnegative_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return value


@dataclass(frozen=True)
class Config:
    """Validated settings; no I/O is performed while constructing it."""

    db_path: Path
    host: str = "0.0.0.0"
    port: int = 8765
    memory_mcp_url: str = "http://127.0.0.1:8080"
    memory_mcp_transport: str = "streamable_http"
    opencode_url: str = "http://127.0.0.1:4096"
    supervisor_model: str = "deepseek/deepseek-v4-flash-0731"
    supervisor_provider: str = "openrouter"
    model_api_key: str | None = None
    model_base_url: str = "https://openrouter.ai/api/v1"
    memory_category_id: str | None = None
    opencode_timeout: float = 30.0
    opencode_healthcheck: bool = True
    inbox_batch_size: int = 20
    inbox_max_pending: int = 10_000
    processing_lease_seconds: float = 60.0
    max_attempts: int = 3
    runtime_poll_interval: float = 1.0
    shutdown_timeout: float = 5.0
    checkpoint_id: str = "grams-supervisor"
    working_context_size: int = 20
    log_level: str = "INFO"
    log_color: str = "auto"
    log_lag_warn_ms: float | None = None
    log_file: Path | None = None
    stagnation_enabled: bool = True
    stagnation_threshold_seconds: float = 1800.0
    supervisor_tick_interval: float = 300.0

    def __post_init__(self) -> None:
        if not str(self.db_path).strip():
            raise ValueError("db_path must not be empty")
        if not self.host.strip() or self.port <= 0 or self.port > 65535:
            raise ValueError("host and port must be valid")
        if self.inbox_batch_size <= 0 or self.inbox_max_pending <= 0:
            raise ValueError("inbox limits must be positive")
        if self.processing_lease_seconds <= 0 or self.runtime_poll_interval <= 0:
            raise ValueError("runtime intervals must be positive")
        if self.max_attempts <= 0 or self.shutdown_timeout <= 0:
            raise ValueError("attempts and shutdown timeout must be positive")
        if not all(math.isfinite(value) for value in (self.processing_lease_seconds, self.runtime_poll_interval, self.shutdown_timeout)):
            raise ValueError("runtime intervals must be finite")
        if self.working_context_size <= 0 or not self.checkpoint_id.strip():
            raise ValueError("working_context_size and checkpoint_id must be valid")
        if self.memory_mcp_transport != "streamable_http" or not self.supervisor_model.strip():
            raise ValueError("memory transport and model must be valid")
        if not self.model_base_url.strip():
            raise ValueError("model_base_url must not be empty")
        if not self.memory_mcp_url.strip() or not self.opencode_url.strip():
            raise ValueError("MCP and OpenCode URLs must not be empty")
        _url("memory_mcp_url", self.memory_mcp_url)
        _url("opencode_url", self.opencode_url)
        _url("model_base_url", self.model_base_url)
        if not math.isfinite(self.opencode_timeout) or self.opencode_timeout <= 0:
            raise ValueError("opencode timeout must be positive and finite")
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level must be a valid Python logging level")
        if self.log_color not in {"auto", "always", "never"}:
            raise ValueError("log_color must be auto, always, or never")
        if self.log_lag_warn_ms is not None and (not math.isfinite(self.log_lag_warn_ms) or self.log_lag_warn_ms < 0):
            raise ValueError("log_lag_warn_ms must be finite and non-negative")
        if not math.isfinite(self.stagnation_threshold_seconds) or self.stagnation_threshold_seconds < 0:
            raise ValueError("stagnation threshold must be finite and non-negative")
        if not math.isfinite(self.supervisor_tick_interval) or self.supervisor_tick_interval <= 0:
            raise ValueError("supervisor tick interval must be positive and finite")

    @classmethod
    def from_env(cls) -> "Config":
        default_db = Path.home() / "Library" / "Application Support" / "grams" / "supervisor.db"
        raw_db = os.getenv("GRAMS_SUPERVISOR_DB_PATH", os.getenv("GRAMS_DB_PATH", str(default_db)))
        checkpoint_id = os.getenv("GRAMS_CHECKPOINT_ID", "grams-supervisor")
        if not raw_db.strip() or not checkpoint_id.strip():
            raise ValueError("database path and checkpoint id must not be empty")
        deployment = os.getenv("OPENROUTER_DEPLOYMENT", "").strip()
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        model_base_url = os.getenv("OPENROUTER_BASE_URL", "").strip()
        if not deployment or not api_key or not model_base_url:
            raise ValueError("OPENROUTER_DEPLOYMENT, OPENROUTER_API_KEY, and OPENROUTER_BASE_URL are required")
        memory_url = os.getenv("GRAMS_MCP_URL", os.getenv("GRAMS_MEMORY_MCP_URL", "http://127.0.0.1:8080"))
        opencode_url = os.getenv("OPENCODE_BASE_URL", os.getenv("GRAMS_OPENCODE_URL", "http://127.0.0.1:4096"))
        return cls(
            db_path=Path(raw_db).expanduser(),
            host=os.getenv("GRAMS_SUPERVISOR_HOST", "0.0.0.0"),
            port=_positive_int("GRAMS_SUPERVISOR_PORT", 8765),
            memory_mcp_url=memory_url,
            memory_mcp_transport=os.getenv("GRAMS_MEMORY_MCP_TRANSPORT", "streamable_http"),
            opencode_url=opencode_url,
            supervisor_model=deployment,
            supervisor_provider=os.getenv("GRAMS_SUPERVISOR_PROVIDER", "openrouter"),
            model_api_key=api_key,
            model_base_url=model_base_url,
            memory_category_id=os.getenv("GRAMS_MEMORY_CATEGORY_ID") or None,
            opencode_timeout=_positive_float("GRAMS_OPENCODE_TIMEOUT", 30.0),
            opencode_healthcheck=_boolean("GRAMS_OPENCODE_HEALTHCHECK", True),
            inbox_batch_size=_positive_int("GRAMS_INBOX_BATCH_SIZE", 20),
            inbox_max_pending=_positive_int("GRAMS_INBOX_MAX_PENDING", 10_000),
            processing_lease_seconds=_positive_float("GRAMS_PROCESSING_LEASE_SECONDS", 60.0),
            max_attempts=_positive_int("GRAMS_MAX_ATTEMPTS", 3),
            runtime_poll_interval=_positive_float("GRAMS_RUNTIME_POLL_INTERVAL", 1.0),
            shutdown_timeout=_positive_float("GRAMS_SHUTDOWN_TIMEOUT", 5.0),
            checkpoint_id=checkpoint_id,
            working_context_size=_positive_int("GRAMS_WORKING_CONTEXT_SIZE", 20),
            log_level=os.getenv("GRAMS_LOG_LEVEL", "INFO"),
            log_color=_log_color("GRAMS_LOG_COLOR", "auto"),
            log_lag_warn_ms=_optional_nonnegative_float("GRAMS_LOG_LAG_WARN_MS"),
            log_file=Path(os.environ["GRAMS_LOG_FILE"]).expanduser() if os.getenv("GRAMS_LOG_FILE") else None,
            stagnation_enabled=_boolean("GRAMS_STAGNATION_ENABLED", True),
            stagnation_threshold_seconds=(
                _optional_nonnegative_float("GRAMS_STAGNATION_THRESHOLD_SECONDS")
                if os.getenv("GRAMS_STAGNATION_THRESHOLD_SECONDS") is not None
                else 1800.0
            ),
            supervisor_tick_interval=_positive_float("GRAMS_SUPERVISOR_TICK_INTERVAL", 300.0),
        )
