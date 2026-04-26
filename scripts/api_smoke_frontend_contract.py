"""Smoke-check the API contract consumed by the Vue frontend.

This script intentionally uses FastAPI TestClient rather than a running server:
it is cheap enough for handover validation, catches auth/permission regressions,
and does not depend on a local browser or port availability.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    # The demo credentials are allowed only outside production. Smoke tests run
    # in development mode so they can validate the packaged demo contract.
    os.environ.setdefault("NES_APP_ENV", "development")

    from fastapi.testclient import TestClient
    from new_energy_sys.api.main import app

    client = TestClient(app)

    unauth = client.get("/api/config")
    if unauth.status_code != 401:
        raise AssertionError(f"Expected unauthenticated /api/config to return 401, got {unauth.status_code}")

    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    if login.status_code != 200:
        raise AssertionError(f"Expected admin login to return 200, got {login.status_code}: {login.text}")

    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    endpoints = [
        "/api/config",
        "/api/models/main",
        "/api/predictions/main?limit=5",
        "/api/reports/list",
        "/api/tasks/commands",
    ]
    for endpoint in endpoints:
        response = client.get(endpoint, headers=headers)
        if response.status_code != 200:
            raise AssertionError(f"Expected {endpoint} to return 200, got {response.status_code}: {response.text}")

    guest_login = client.post("/api/auth/login", json={"username": "guest", "password": "guest123"})
    if guest_login.status_code != 200:
        raise AssertionError(f"Expected guest login to return 200, got {guest_login.status_code}: {guest_login.text}")

    guest_headers = {"Authorization": f"Bearer {guest_login.json()['token']}"}
    forbidden = client.post("/api/tasks/submit", json={"command_id": "unknown"}, headers=guest_headers)
    if forbidden.status_code != 403:
        raise AssertionError(f"Expected guest task submit to return 403, got {forbidden.status_code}: {forbidden.text}")

    print("api smoke checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"api smoke checks failed: {exc}", file=sys.stderr)
        raise
