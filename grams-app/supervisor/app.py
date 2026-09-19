"""Composition root for the GRAMS event receiver application."""

from contextlib import asynccontextmanager
import asyncio
import logging
from typing import AsyncIterator

from fastapi import FastAPI

from supervisor.api import register_event_routes
from supervisor.agent.runtime import build_runtime
from supervisor.agent.services import JevClient, OpenRouterClient
from supervisor.agent.worker import SupervisorWorker
from supervisor.config import Config
from supervisor.inbox import EventInbox, InboxRepository
from supervisor.memory.client import MCPMemoryClient
from supervisor.opencode.client import OpenCodeClient
from supervisor.platform.sqlite.db import open_connection
from supervisor.observability import configure_logging, elapsed_ms, emit, monotonic_ns

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
        worker = None
        worker_task = None
        memory = None
        jev = None
        openrouter = None
        opencode = None
        try:
            await inbox.initialize()
            app.state.config = settings
            app.state.connection = connection
            app.state.repository = repository
            app.state.inbox = inbox
            app.state.accepting = True
            if settings.worker_enabled:
                memory = MCPMemoryClient(settings.memory_mcp_url)
                jev = JevClient()
                openrouter = OpenRouterClient()
                opencode = OpenCodeClient(settings.opencode_base_url)
                runtime = build_runtime(
                    inbox=inbox,
                    memory=memory,
                    jev=jev,
                    openrouter=openrouter,
                    opencode=opencode,
                    batch_size=settings.inbox_batch_size,
                )
                worker = SupervisorWorker(
                    runtime,
                    inbox,
                    opencode,
                    poll_seconds=settings.worker_poll_seconds,
                )
                worker_task = asyncio.create_task(worker.run_forever(), name="grams-supervisor-worker")
                app.state.worker = worker
            emit(logger, logging.INFO, "receiver_startup", status="ready", duration_ms=elapsed_ms(startup_started))
            try:
                yield
            finally:
                app.state.accepting = False
                if worker is not None:
                    worker.stop()
                if worker_task is not None:
                    try:
                        await asyncio.wait_for(worker_task, timeout=settings.processing_lease_seconds)
                    except TimeoutError:
                        worker_task.cancel()
                        await asyncio.gather(worker_task, return_exceptions=True)
                shutdown_started = monotonic_ns()
                emit(logger, logging.INFO, "receiver_shutdown", status="stopped", duration_ms=elapsed_ms(shutdown_started))
        finally:
            for component, client in (
                ("opencode", opencode),
                ("openrouter", openrouter),
                ("jev", jev),
                ("memory", memory),
            ):
                if client is None:
                    continue
                try:
                    await client.aclose()
                except Exception as error:
                    emit(logger, logging.ERROR, "shutdown_component_failed", component=component,
                         error=type(error).__name__, detail=str(error))
            try:
                await connection.close()
            except Exception as error:
                emit(logger, logging.ERROR, "shutdown_component_failed", component="sqlite",
                     error=type(error).__name__, detail=str(error))

    app = FastAPI(redirect_slashes=False, lifespan=lifespan)
    register_event_routes(app)
    return app


app = create_app()
