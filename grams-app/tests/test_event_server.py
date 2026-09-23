import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from fastapi.testclient import TestClient
from supervisor.app import create_app
from supervisor.config import Config


def make_client():
    temporary_directory = tempfile.TemporaryDirectory()
    config = Config(Path(temporary_directory.name) / "supervisor.db")
    return temporary_directory, TestClient(create_app(config))


def event_request(payload, **extra):
    return {
        "schema_version": 1,
        "type": "USER_MESSAGE_FINAL",
        "source_event": "message.part.updated",
        "timestamp": "2026-09-23T12:00:00.000Z",
        "session_id": "session-1",
        "payload": payload,
        **extra,
    }


def test_valid_event_payload_values_are_preserved_in_the_inbox():
    temporary_directory, client = make_client()
    with temporary_directory, client:
        values = [{"message": "cafe"}, [1, "two"], "value", 42, True, None]
        for value in values:
            response = client.post("/events", json=event_request(value))
            assert response.status_code == 202
            assert response.content == b""

        with sqlite3.connect(client.app.state.config.db_path) as database:
            rows = database.execute("SELECT status, payload FROM supervisor_events").fetchall()
        assert len(rows) == len(values)
        assert all(status == "PENDING" for status, _ in rows)
        assert [json.loads(payload)["payload"] for _, payload in rows] == values


def test_invalid_event_schema_returns_unprocessable_entity_without_inserting():
    temporary_directory, client = make_client()
    with temporary_directory, client:
        for payload in [
            {"type": "USER_MESSAGE_FINAL"},
            event_request({}, schema_version=2),
            event_request({}, sequence=True),
            event_request({}, timestamp="not-a-timestamp"),
        ]:
            response = client.post("/events", json=payload)
            assert response.status_code == 422
            assert "event schema" in response.json()["detail"]

        with sqlite3.connect(client.app.state.config.db_path) as database:
            assert database.execute("SELECT COUNT(*) FROM supervisor_events").fetchone() == (0,)


def test_unknown_event_fields_are_accepted_and_preserved():
    temporary_directory, client = make_client()
    payload = event_request({"text": "hello"}, plugin_extension={"version": 2})
    with temporary_directory, client:
        response = client.post("/events", json=payload)
        assert response.status_code == 202

        with sqlite3.connect(client.app.state.config.db_path) as database:
            stored = database.execute("SELECT payload FROM supervisor_events").fetchone()[0]
        assert json.loads(stored) == payload


def test_openapi_documents_the_event_request_fields():
    temporary_directory, client = make_client()
    with temporary_directory, client:
        schema = client.get("/openapi.json").json()
        request_schema = schema["paths"]["/events"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert set(request_schema["required"]) >= {
            "schema_version", "type", "source_event", "timestamp", "session_id", "payload",
        }
        assert "payload" in request_schema["properties"]


def test_invalid_json_returns_bad_request_without_inserting():
    temporary_directory, client = make_client()
    with temporary_directory, client:
        response = client.post("/events", content=b"{invalid")

        assert response.status_code == 400
        assert "request body must be JSON" in response.json()["detail"]
        with sqlite3.connect(client.app.state.config.db_path) as database:
            assert database.execute("SELECT COUNT(*) FROM supervisor_events").fetchone() == (0,)


def test_nonstandard_json_constants_are_rejected():
    temporary_directory, client = make_client()
    with temporary_directory, client:
        response = client.post("/events", content=b"NaN")

        assert response.status_code == 400
        with sqlite3.connect(client.app.state.config.db_path) as database:
            assert database.execute("SELECT COUNT(*) FROM supervisor_events").fetchone() == (0,)


def test_repeated_payload_ids_are_separate_receptions():
    temporary_directory, client = make_client()
    with temporary_directory, client:
        for _ in range(2):
            response = client.post(
                "/events",
                json=event_request({}, id="same"),
            )
            assert response.status_code == 202

        with sqlite3.connect(client.app.state.config.db_path) as database:
            assert database.execute("SELECT COUNT(*) FROM supervisor_events").fetchone() == (2,)


def test_unsupported_routes_and_methods_are_rejected():
    temporary_directory, client = make_client()
    with temporary_directory, client:
        assert client.get("/missing").status_code == 404
        assert client.post("/missing", json={}).status_code == 404
        assert client.get("/events").status_code == 405
        assert client.post("/events/", json={}).status_code == 404


def test_lifespan_starts_explicit_supervisor_worker_when_enabled():
    temporary_directory = tempfile.TemporaryDirectory()
    config = Config(
        Path(temporary_directory.name) / "supervisor.db",
        worker_enabled=True,
        worker_poll_seconds=0.01,
    )
    with temporary_directory, TestClient(create_app(config)) as client:
        assert client.app.state.worker is not None
