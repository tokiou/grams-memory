"""Composition root for the GRAMS Supervisor ASGI application."""

from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

from fastapi import FastAPI

from .api import register_event_routes
from .config import Config
from .inbox import EventInbox, InboxRepository
from .memory import MCPMemoryClient
from .opencode import OpenCodeClient
from .platform.sqlite.db import close_checkpointer, open_checkpointer, open_connection
from .agent import build_graph
from .agent.nodes.review import OpenAIReviewModel, ReviewModel
from .runtime import SupervisorRuntime
from .observability import configure_logging, elapsed_ms, emit, monotonic_ns, safe_endpoint

logger = logging.getLogger(__name__)


def create_app(
    config: Config | None = None,
    *,
    memory_client: object | None = None,
    opencode_client: object | None = None,
    review_model: ReviewModel | None = None,
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
        checkpointer_context = None
        runtime: SupervisorRuntime | None = None
        memory = None
        opencode = None
        model = None
        memory_manifest = {"projects": []}
        try:
            if review_model is None and not settings.model_api_key:
                raise ValueError("OPENROUTER_API_KEY is required for the production Supervisor")
            await inbox.initialize()
            saver, checkpointer_context = await open_checkpointer(settings.db_path, connection)
            memory = memory_client or MCPMemoryClient(settings.memory_mcp_url or "http://127.0.0.1:8080")
            opencode = opencode_client or OpenCodeClient(settings.opencode_url or "http://127.0.0.1:4096", timeout=settings.opencode_timeout)
            model = review_model or OpenAIReviewModel(settings.model_base_url, settings.model_api_key, settings.supervisor_model)
            memory_category_id = settings.memory_category_id
            if isinstance(memory, MCPMemoryClient):
                await memory.list_tools()
                memory_manifest = await memory.get_manifest()
            if isinstance(opencode, OpenCodeClient) and settings.opencode_healthcheck:
                await opencode.healthcheck()
            if isinstance(model, OpenAIReviewModel):
                await model.healthcheck()
            graph = build_graph(
                inbox,
                memory,
                opencode,
                saver,
                config=settings,
                review_model=model,
                memory_category_id=memory_category_id,
                memory_manifest=memory_manifest,
            )
            runtime = SupervisorRuntime(
                inbox,
                graph,
                checkpoint_id=settings.checkpoint_id,
                poll_interval=settings.runtime_poll_interval,
                tick_interval=settings.supervisor_tick_interval,
                lag_warn_ms=settings.log_lag_warn_ms,
            )
            app.state.config = settings
            app.state.connection = connection
            app.state.repository = repository
            app.state.inbox = inbox
            app.state.memory = memory
            app.state.opencode = opencode
            app.state.checkpointer = saver
            app.state.runtime = runtime
            app.state.accepting = True
            await runtime.start()
            emit(logger, logging.INFO, "supervisor_startup", status="ready", duration_ms=elapsed_ms(startup_started),
                 memory_mcp_url=safe_endpoint(settings.memory_mcp_url), opencode_url=safe_endpoint(settings.opencode_url))
            try:
                yield
            finally:
                app.state.accepting = False
                shutdown_started = monotonic_ns()
                if runtime is not None:
                    await runtime.stop(settings.shutdown_timeout)
                emit(logger, logging.INFO, "supervisor_shutdown", status="stopped", duration_ms=elapsed_ms(shutdown_started))
        finally:
            if checkpointer_context is not None:
                try:
                    await close_checkpointer(checkpointer_context)
                except Exception as error:
                    emit(logger, logging.ERROR, "shutdown_component_failed", component="checkpointer",
                         error=type(error).__name__, detail=str(error))
            try:
                await inbox.close()
            except Exception as error:
                emit(logger, logging.ERROR, "shutdown_component_failed", component="inbox",
                     error=type(error).__name__, detail=str(error))
            try:
                await connection.close()
            except Exception as error:
                emit(logger, logging.ERROR, "shutdown_component_failed", component="sqlite",
                     error=type(error).__name__, detail=str(error))
            for client in (memory, opencode, model):
                if client is None:
                    continue
                close = getattr(client, "aclose", None)
                if close is not None:
                    try:
                        await close()
                    except Exception as error:
                        emit(logger, logging.ERROR, "shutdown_component_failed", component=type(client).__name__,
                             error=type(error).__name__, detail=str(error))

    app = FastAPI(redirect_slashes=False, lifespan=lifespan)
    register_event_routes(app)
    return app


app = create_app()
