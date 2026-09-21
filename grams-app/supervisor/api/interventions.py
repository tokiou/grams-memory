"""Session-scoped pending intervention API used by the OpenCode plugin."""

import json
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from supervisor.interventions import PendingInterventionRepository
from supervisor.observability import emit

logger = logging.getLogger(__name__)


async def _json_object(request: Request) -> dict[str, object] | None:
    try:
        value = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def register_intervention_routes(app: FastAPI) -> None:
    @app.post("/interventions/claim")
    async def claim_intervention(request: Request):
        body = await _json_object(request)
        session_id = body.get("session_id") if body else None
        if not isinstance(session_id, str) or not session_id.strip():
            return JSONResponse(status_code=400, content={"detail": "session_id is required"})
        try:
            result = await request.app.state.interventions.claim(session_id)
        except Exception:
            emit(logger, logging.ERROR, "intervention_claim_failed", session_id=session_id)
            return JSONResponse(status_code=503, content={"detail": "intervention store unavailable"})
        if result is None:
            return {"intervention": None}
        emit(
            logger,
            logging.INFO,
            "INTERVENTION_CLAIMED",
            session_id=session_id,
            intervention_id=result["id"],
        )
        return {
            "intervention": {
                "id": result["id"],
                "session_id": result["session_id"],
                "message": result["message"],
                "delivery_key": result["delivery_key"],
                "claim_token": result["claim_token"],
            }
        }

    @app.post("/interventions/{intervention_id}/consume")
    async def consume_intervention(intervention_id: str, request: Request):
        body = await _json_object(request)
        session_id = body.get("session_id") if body else None
        claim_token = body.get("claim_token") if body else None
        if not all(isinstance(value, str) and value.strip() for value in (session_id, claim_token)):
            return JSONResponse(
                status_code=400,
                content={"detail": "session_id and claim_token are required"},
            )
        try:
            consumed = await request.app.state.interventions.consume(
                intervention_id,
                session_id,
                claim_token,
            )
        except Exception:
            emit(
                logger,
                logging.ERROR,
                "intervention_consume_failed",
                session_id=session_id,
                intervention_id=intervention_id,
            )
            return JSONResponse(status_code=503, content={"detail": "intervention store unavailable"})
        if not consumed:
            return JSONResponse(status_code=409, content={"detail": "intervention claim is invalid"})
        emit(
            logger,
            logging.INFO,
            "INTERVENTION_CONSUMED",
            session_id=session_id,
            intervention_id=intervention_id,
        )
        return {"consumed": True}
