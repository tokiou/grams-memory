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


def test_valid_json_values_are_persisted_in_the_inbox():
    temporary_directory, client = make_client()
    with temporary_directory, client:
        for kwargs in [
            {"json": {"message": "cafe"}},
            {"json": [1, "two"]},
            {"json": "value"},
            {"json": 42},
            {"content": b"null"},
        ]:
            response = client.post("/events", **kwargs)
            assert response.status_code == 202
            assert response.content == b""

        with sqlite3.connect(client.app.state.config.db_path) as database:
            rows = database.execute("SELECT status FROM supervisor_events").fetchall()
        assert len(rows) == 5
        assert all(status == "PENDING" for (status,) in rows)


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
            response = client.post("/events", json={"id": "same", "type": "MESSAGE_FINAL"})
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
