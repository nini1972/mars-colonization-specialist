"""Live integration tests for the correlation DAG fragment.

These tests seed the in-memory TelemetryQueryService singleton directly
(no mocking) and call the real FastAPI route to verify the rendered HTML.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

import mars_agent.dashboard_app as dashboard_app
import mars_agent.mcp.server as mcp_server


@pytest.fixture(autouse=True)
def _clear_telemetry() -> Generator[None, None, None]:
    """Isolate each test by clearing the in-memory event deque after the test."""
    yield
    mcp_server._TELEMETRY._events.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(dashboard_app.app)


def _seed_full_chain(correlation_id: str = "integ-full") -> None:
    for i, (tool, recorded_at) in enumerate(
        [
            ("mars.plan", "2026-04-07T10:00:00.000+00:00"),
            ("mars.simulate", "2026-04-07T10:01:00.000+00:00"),
            ("mars.governance", "2026-04-07T10:02:00.000+00:00"),
            ("mars.benchmark", "2026-04-07T10:03:00.000+00:00"),
        ]
    ):
        mcp_server._TELEMETRY.record(
            {
                "recorded_at": recorded_at,
                "request_id": f"req-{i}",
                "event": "tool.success",
                "tool": tool,
                "correlation_id": correlation_id,
                "mission_id": "mars-integ",
                "outcome": "success",
                "latency_ms": 10.0,
                "auth_enabled": True,
                "error_code": None,
            }
        )


def test_correlation_fragment_full_chain_shows_four_present_nodes(
    client: TestClient,
) -> None:
    """Full happy-path chain: four nodes all rendered as dag-node-present."""
    _seed_full_chain()

    response = client.get("/dashboard/fragments/correlation?correlation_id=integ-full")

    assert response.status_code == 200
    assert response.text.count("dag-node-present") == 4
    assert "dag-node-missing" not in response.text
    assert "dag-node-orphaned" not in response.text
    assert "integrity-complete" in response.text


def test_correlation_fragment_partial_chain_orphan_nodes_detected(
    client: TestClient,
) -> None:
    """Plan + governance only (simulate absent) → simulate missing, governance orphaned."""
    mcp_server._TELEMETRY.record(
        {
            "recorded_at": "2026-04-07T10:00:00.000+00:00",
            "request_id": "req-p1",
            "event": "tool.success",
            "tool": "mars.plan",
            "correlation_id": "integ-partial",
            "mission_id": "mars-integ",
            "outcome": "success",
            "latency_ms": 10.0,
            "auth_enabled": True,
            "error_code": None,
        }
    )
    mcp_server._TELEMETRY.record(
        {
            "recorded_at": "2026-04-07T10:01:00.000+00:00",
            "request_id": "req-p2",
            "event": "tool.success",
            "tool": "mars.governance",
            "correlation_id": "integ-partial",
            "mission_id": "mars-integ",
            "outcome": "success",
            "latency_ms": 12.0,
            "auth_enabled": True,
            "error_code": None,
        }
    )

    response = client.get("/dashboard/fragments/correlation?correlation_id=integ-partial")

    assert response.status_code == 200
    assert "dag-node-present" in response.text
    assert "dag-node-missing" in response.text
    assert "dag-node-orphaned" in response.text
    assert "integrity-partial" in response.text
