import sqlite3
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import sys
sys.path.insert(0, str(Path(__file__).parents[1]))

from supervisor.app import create_app
from supervisor.config import Config


def test_intervention_claim_and_consume_api_is_session_scoped():
    temporary_directory = tempfile.TemporaryDirectory()
    config = Config(Path(temporary_directory.name) / "supervisor.db")
    with temporary_directory, TestClient(create_app(config)) as client:
        with sqlite3.connect(config.db_path) as database:
            database.execute(
                """INSERT INTO supervisor_interventions
                (id, session_id, message, delivery_key, status, created_at)
                VALUES ('intervention-1', 'session-1', 'Inject this.', 'cycle-1', 'PENDING', '2026-01-01T00:00:00+00:00')"""
            )
            database.commit()

        assert client.post("/interventions/claim", json={"session_id": "session-2"}).json() == {
            "intervention": None
        }
        response = client.post("/interventions/claim", json={"session_id": "session-1"})
        assert response.status_code == 200
        intervention = response.json()["intervention"]
        assert intervention["message"] == "Inject this."

        consumed = client.post(
            "/interventions/intervention-1/consume",
            json={"session_id": "session-1", "claim_token": intervention["claim_token"]},
        )
        assert consumed.status_code == 200
        assert consumed.json() == {"consumed": True}
        assert client.post("/interventions/claim", json={"session_id": "session-1"}).json() == {
            "intervention": None
        }


def test_intervention_api_rejects_missing_session_id():
    temporary_directory = tempfile.TemporaryDirectory()
    config = Config(Path(temporary_directory.name) / "supervisor.db")
    with temporary_directory, TestClient(create_app(config)) as client:
        response = client.post("/interventions/claim", json={})
        assert response.status_code == 400


def test_intervention_api_rejects_default_session_id():
    temporary_directory = tempfile.TemporaryDirectory()
    config = Config(Path(temporary_directory.name) / "supervisor.db")
    with temporary_directory, TestClient(create_app(config)) as client:
        assert client.post("/interventions/claim", json={"session_id": "default"}).status_code == 400
        assert client.post(
            "/interventions/intervention-1/consume",
            json={"session_id": "default", "claim_token": "token"},
        ).status_code == 400
