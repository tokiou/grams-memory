"""FastAPI event ingress. It never executes the Supervisor graph."""

import json
import logging
import math

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from supervisor.api.schema import EventRequest
from supervisor.inbox import SupervisorEventInput
from supervisor.observability import emit

logger = logging.getLogger(__name__)


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _validate_finite_json(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if isinstance(value, list):
        for item in value:
            _validate_finite_json(item)
    elif isinstance(value, dict):
        for item in value.values():
            _validate_finite_json(item)


def register_event_routes(app: FastAPI) -> None:
    @app.post(
        "/events",
        status_code=202,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": EventRequest.model_json_schema()},
                },
            },
        },
    )
    async def receive_event(request: Request) -> Response:
        if not getattr(request.app.state, "accepting", True):
            return JSONResponse(status_code=503, content={"detail": "event inbox is shutting down"})
        body = await request.body()
        try:
            payload = json.loads(body, parse_constant=_reject_nonstandard_constant)
            _validate_finite_json(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            return JSONResponse(status_code=400, content={"detail": "request body must be JSON"})

        try:
            EventRequest.model_validate(payload)
        except ValidationError as error:
            errors = [
                {
                    "field": ".".join(str(part) for part in item["loc"]),
                    "message": item["msg"],
                    "type": item["type"],
                }
                for item in error.errors()
            ]
            return JSONResponse(
                status_code=422,
                content={"detail": "request body does not match the event schema", "errors": errors},
            )

        event = None
        try:
            event = SupervisorEventInput.from_payload(payload)
            emit(logger, logging.INFO, "event_received", ingress_id=event.ingress_id, source_event_id=event.id, session_id=event.session_id,
                 root_session_id=event.root_session_id, event_type=event.type,
                 source_event=event.source_event, source_run_id=event.run_id)
            await request.app.state.inbox.persist(event)
        except Exception:
            emit(logger, logging.ERROR, "event_persistence_failed", event_type=getattr(event, "type", None),
                 error="persistence_failed")
            return JSONResponse(status_code=503, content={"detail": "event inbox unavailable"})
        return Response(status_code=202)
