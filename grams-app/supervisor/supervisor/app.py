"""Composition root for the GRAMS event receiver application."""

from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

from fastapi import FastAPI

from .api import register_event_routes
from .config import Config
from .inbox import EventInbox, InboxRepository
from .platform.sqlite.db import open_connection
from .observability import configure_logging, elapsed_ms, emit, monotonic_ns

logger = logging.getLogger(__name__)


def create_app(
    config: Config | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = config or Config.from_env()
        configure_logging(settings.log_level.upper(), settings.log_color, str(settings.log_file) if settings.log_file else None)
        startup_started = monotonic_ns()
        emit(logger, logging.INFO, "supervisor_startup", status="starting", log_level=settings.log_level.upper(), log_color=settings.log_color)
        connection = await open_connection(settings.db_path)
        repository = InboxRepository(connection, max_attempts=settings.max_attempts)
        inbox = EventInbox(
            repository,
            batch_size=settings.inbox_batch_size,
            lease_seconds=settings.processing_lease_seconds,
            lag_warn_ms=settings.log_lag_warn_ms,
        )
        try:
            await inbox.initialize()
            app.state.config = settings
            app.state.connection = connection
            app.state.repository = repository
            app.state.inbox = inbox
            app.state.accepting = True
            emit(logger, logging.INFO, "receiver_startup", status="ready", duration_ms=elapsed_ms(startup_started))
            try:
                yield
            finally:
                app.state.accepting = False
                shutdown_started = monotonic_ns()
                emit(logger, logging.INFO, "receiver_shutdown", status="stopped", duration_ms=elapsed_ms(shutdown_started))
        finally:
            try:
                await connection.close()
            except Exception as error:
                emit(logger, logging.ERROR, "shutdown_component_failed", component="sqlite",
                     error=type(error).__name__, detail=str(error))

    app = FastAPI(redirect_slashes=False, lifespan=lifespan)
    register_event_routes(app)
    return app


app = create_app()
