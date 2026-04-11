"""Tests for the /health and /ready probe endpoints."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import mars_agent.dashboard_app as dashboard_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(dashboard_app.app)


def _dashboard_snapshot(*, degraded: bool) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "overview_panel": {
            "schema_version": "1.0",
            "health": {"status": "healthy", "latest_recorded_at": "2026-04-01T00:00:00+00:00"},
            "counts": {"events": 0, "successes": 0, "failures": 0},
        },
        "invocation_panel": {
            "schema_version": "1.0",
            "items": [],
            "page": 1,
            "page_size": 20,
            "total": 0,
            "has_next_page": False,
        },
        "correlation_panel": {"schema_version": "1.0", "items": []},
        "replay_degraded_panel": {
            "persistence_backend": "memory" if degraded else "sqlite",
            "persistence_degraded": degraded,
            "idempotency_completed_entries": 0,
            "idempotency_in_flight_entries": 0,
        },
    }


class TestHealthEndpoint:
    def test_health_returns_200(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_ok_status(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.json() == {"status": "ok"}

    def test_health_content_type_is_json(self, client: TestClient) -> None:
        response = client.get("/health")
        assert "application/json" in response.headers["content-type"]


class TestReadyEndpoint:
    @pytest.fixture
    def _patch_snapshot_ready(self) -> Generator[None, None, None]:
        with patch.object(
            dashboard_app,
            "telemetry_dashboard_snapshot",
            return_value=_dashboard_snapshot(degraded=False),
        ):
            yield

    @pytest.fixture
    def _patch_snapshot_degraded(self) -> Generator[None, None, None]:
        with patch.object(
            dashboard_app,
            "telemetry_dashboard_snapshot",
            return_value=_dashboard_snapshot(degraded=True),
        ):
            yield

    @pytest.fixture
    def _patch_snapshot_raises(self) -> Generator[None, None, None]:
        with patch.object(
            dashboard_app,
            "telemetry_dashboard_snapshot",
            side_effect=RuntimeError("backend unavailable"),
        ):
            yield

    def test_ready_returns_200_when_sqlite(
        self, client: TestClient, _patch_snapshot_ready: None
    ) -> None:
        response = client.get("/ready")
        assert response.status_code == 200

    def test_ready_body_when_sqlite(
        self, client: TestClient, _patch_snapshot_ready: None
    ) -> None:
        response = client.get("/ready")
        assert response.json() == {"status": "ready"}

    def test_ready_returns_503_when_degraded(
        self, client: TestClient, _patch_snapshot_degraded: None
    ) -> None:
        response = client.get("/ready")
        assert response.status_code == 503

    def test_ready_body_when_degraded(
        self, client: TestClient, _patch_snapshot_degraded: None
    ) -> None:
        response = client.get("/ready")
        assert response.json() == {"status": "degraded"}

    def test_ready_returns_503_on_exception(
        self, client: TestClient, _patch_snapshot_raises: None
    ) -> None:
        response = client.get("/ready")
        assert response.status_code == 503

    def test_ready_body_on_exception(
        self, client: TestClient, _patch_snapshot_raises: None
    ) -> None:
        response = client.get("/ready")
        assert response.json() == {"status": "degraded"}
