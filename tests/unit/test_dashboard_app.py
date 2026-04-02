from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import mars_agent.dashboard_app as dashboard_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(dashboard_app.app)


def _sample_dashboard_payload(*, degraded: bool = False) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "overview_panel": {
            "schema_version": "1.0",
            "health": {
                "status": "degraded" if degraded else "healthy",
                "latest_recorded_at": "2026-04-02T12:00:00.000+00:00",
            },
            "counts": {
                "events": 3,
                "successes": 2,
                "failures": 1,
            },
        },
        "invocation_panel": {
            "schema_version": "1.0",
            "items": [
                {
                    "recorded_at": "2026-04-02T11:59:59.000+00:00",
                    "event": "tool.success",
                    "tool": "mars.plan",
                    "request_id": "req-2",
                    "correlation_id": "corr-2",
                    "mission_id": "mars-demo",
                    "outcome": "success",
                    "latency_ms": 10.0,
                    "auth_enabled": True,
                    "error_code": None,
                },
                {
                    "recorded_at": "2026-04-02T11:59:58.000+00:00",
                    "event": "tool.error",
                    "tool": "mars.simulate",
                    "request_id": "req-1",
                    "correlation_id": "corr-1",
                    "mission_id": "mars-demo",
                    "outcome": "failure",
                    "latency_ms": 20.0,
                    "auth_enabled": True,
                    "error_code": "invalid_request",
                },
            ],
            "page": 1,
            "page_size": 20,
            "total": 2,
            "has_next_page": False,
        },
        "correlation_panel": {
            "hint": "Use telemetry_correlation_chain(correlation_id) for drill-down",
        },
        "replay_degraded_panel": {
            "persistence_backend": "memory" if degraded else "sqlite",
            "persistence_degraded": degraded,
            "idempotency_completed_entries": 4,
            "idempotency_in_flight_entries": 0,
        },
    }


def _patch_dashboard_snapshot(monkeypatch: pytest.MonkeyPatch, *, degraded: bool = False) -> None:
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_dashboard_snapshot",
        lambda page_size=20: _sample_dashboard_payload(degraded=degraded),
    )


def _patch_invocations(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_list_events(**_: object) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "items": [
                {
                    "recorded_at": "2026-04-02T10:00:01.000+00:00",
                    "event": "tool.success",
                    "tool": "mars.plan",
                    "request_id": "req-b",
                    "correlation_id": "corr",
                    "mission_id": "mars",
                    "outcome": "success",
                    "latency_ms": 11.0,
                    "auth_enabled": True,
                    "error_code": None,
                },
                {
                    "recorded_at": "2026-04-02T10:00:02.000+00:00",
                    "event": "tool.success",
                    "tool": "mars.plan",
                    "request_id": "req-c",
                    "correlation_id": "corr",
                    "mission_id": "mars",
                    "outcome": "success",
                    "latency_ms": 12.0,
                    "auth_enabled": True,
                    "error_code": None,
                },
            ],
            "page": 1,
            "page_size": 20,
            "total": 2,
            "has_next_page": False,
        }

    monkeypatch.setattr(dashboard_app, "telemetry_list_events", _fake_list_events)


def test_dashboard_main_page_exposes_ui_schema_version(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "UI schema version" in response.text
    assert dashboard_app.UI_SCHEMA_VERSION in response.text


def test_dashboard_json_endpoint_keeps_json_contract(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)

    response = client.get("/api/telemetry/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ui_schema_version"] == "1.0"
    assert set(payload["dashboard"].keys()) == {
        "schema_version",
        "overview_panel",
        "invocation_panel",
        "correlation_panel",
        "replay_degraded_panel",
    }


def test_fragment_integrity_rejects_unexpected_dashboard_key(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _invalid_payload(page_size: int = 20) -> dict[str, object]:
        payload = _sample_dashboard_payload()
        payload["unexpected"] = "boom"
        return payload

    monkeypatch.setattr(dashboard_app, "telemetry_dashboard_snapshot", _invalid_payload)

    response = client.get("/dashboard/fragments/overview")

    assert response.status_code == 500
    assert "unexpected" in response.text


def test_invocation_fragment_renders_deterministic_order(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_invocations(monkeypatch)

    response = client.get("/dashboard/fragments/invocations?page=1&page_size=20")

    assert response.status_code == 200
    first = response.text.find("req-c")
    second = response.text.find("req-b")
    assert first != -1
    assert second != -1
    assert first < second


def test_replay_fragment_shows_degraded_mode_banner(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch, degraded=True)

    response = client.get("/dashboard/fragments/replay")

    assert response.status_code == 200
    assert "degraded" in response.text.lower()


def test_dashboard_page_includes_loading_skeleton_markers(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Loading correlation chain" in response.text
    assert "Loading invocation details" in response.text
    assert "Refreshing replay indicators" in response.text


def test_main_page_shows_degraded_banner_when_backend_is_degraded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch, degraded=True)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Persistence is degraded" in response.text


def test_fragment_integrity_markers_exist_for_all_fragment_routes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)
    _patch_invocations(monkeypatch)
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_correlation_chain",
        lambda correlation_id: {
            "schema_version": "1.0",
            "correlation_id": correlation_id,
            "items": [
                {
                    "recorded_at": "2026-04-02T10:00:02.000+00:00",
                    "tool": "mars.plan",
                    "outcome": "success",
                    "error_code": None,
                }
            ],
        },
    )

    overview = client.get("/dashboard/fragments/overview")
    invocations = client.get("/dashboard/fragments/invocations?page=1&page_size=20")
    correlation = client.get("/dashboard/fragments/correlation?correlation_id=corr-1")
    replay = client.get("/dashboard/fragments/replay")

    assert overview.status_code == 200
    assert invocations.status_code == 200
    assert correlation.status_code == 200
    assert replay.status_code == 200

    for payload in (overview.text, invocations.text, correlation.text, replay.text):
        assert "data-fragment" in payload
        assert "data-fragment-required" in payload
        assert "data-fragment-allowed" in payload
