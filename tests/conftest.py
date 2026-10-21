"""All automated checks run against synthetic data without network access."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ARKHAM_API_KEY", raising=False)
    from main import app

    with TestClient(app) as session:
        yield session


@pytest.fixture
def scenarios(client):
    response = client.get("/api/v1/trail/scenarios")
    assert response.status_code == 200
    return {item["id"]: item for item in response.json()["scenarios"]}


@pytest.fixture
def trace(client, scenarios):
    def run(scenario_id):
        response = client.post(
            "/api/v1/trail/trace-report", json=scenarios[scenario_id]["report"]
        )
        assert response.status_code == 200, response.text
        return response.json()

    return run
