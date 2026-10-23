"""All automated checks run against synthetic data without network access."""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolate_cryptoapis_pacing(monkeypatch):
    # Pacing itself is tested with a fake clock in test_cryptoapis_limits.py.
    from services import cryptoapis_continuation
    @asynccontextmanager
    async def no_wait():
        yield
    monkeypatch.setattr(cryptoapis_continuation, 'request_slot', no_wait)
    monkeypatch.setattr(cryptoapis_continuation, 'defer_requests', lambda seconds: None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
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
            "/api/v1/trail/trace-report", json=scenarios[scenario_id]["handoff"]
        )
        assert response.status_code == 200, response.text
        return response.json()

    return run
