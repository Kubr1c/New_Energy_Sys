"""API-layer tests for /api/showcase/scenarios and /api/showcase/summary."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


class TestShowcaseScenarios:
    def test_requires_auth(self, client):
        resp = client.get("/api/showcase/scenarios")
        assert resp.status_code == 401

    def test_returns_rows_with_auth(self, client):
        headers = _auth_headers(client)
        resp = client.get("/api/showcase/scenarios", headers=headers)
        assert resp.status_code == 200
        rows = resp.json()
        assert isinstance(rows, list)
        if len(rows) > 0:
            assert "scenario_name" in rows[0]
            assert "net_incremental_revenue_eur" in rows[0]
            assert "scenario_type" in rows[0]
            assert "boundary_note" in rows[0]

    def test_scenario_types_present(self, client):
        headers = _auth_headers(client)
        resp = client.get("/api/showcase/scenarios", headers=headers)
        rows = resp.json()
        types = {row.get("scenario_type") for row in rows}
        assert "baseline" in types, f"Expected 'baseline' in scenario types, got {types}"


class TestShowcaseSummary:
    def test_requires_auth(self, client):
        resp = client.get("/api/showcase/summary")
        assert resp.status_code == 401

    def test_returns_quality_gates_with_auth(self, client):
        headers = _auth_headers(client)
        resp = client.get("/api/showcase/summary", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "quality_gates" in data
        gates = data["quality_gates"]
        assert "baseline_net_negative" in gates
        assert "at_least_one_positive" in gates
        assert "all_boundary_notes_non_empty" in gates
        assert gates["baseline_net_negative"] is True

    def test_has_scenario_count(self, client):
        headers = _auth_headers(client)
        resp = client.get("/api/showcase/summary", headers=headers)
        data = resp.json()
        assert "scenario_count" in data
        assert data["scenario_count"] == 8

    def test_returns_404_when_file_missing(self, client, monkeypatch):
        """Simulate missing Stage23 summary file."""
        from backend.app import data_loader

        monkeypatch.setattr(data_loader, "get_stage23_summary", lambda: None)
        headers = _auth_headers(client)
        resp = client.get("/api/showcase/summary", headers=headers)
        assert resp.status_code == 404
