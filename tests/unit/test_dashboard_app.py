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


def test_invocation_list_opens_modal_via_custom_event(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_invocations(monkeypatch)

    response = client.get("/dashboard/fragments/invocations?page=1&page_size=20")

    assert response.status_code == 200
    assert "open-invocation-modal" in response.text
    assert "#invocation-detail-modal-body" in response.text


def test_dashboard_has_modal_loading_skeleton_and_close_control(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Loading modal content" in response.text
    assert "@click=\"modalOpen = false\"" in response.text


def test_invocation_detail_fragment_renders_breadcrumb_and_replay_badge(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_invocation_detail",
        lambda request_id: {
            "schema_version": "1.0",
            "item": {
                "request_id": request_id,
                "recorded_at": "2026-04-02T12:00:00.000+00:00",
                "event": "tool.success",
                "tool": "mars.plan",
                "correlation_id": "corr-42",
                "mission_id": "mars-alpha",
                "outcome": "success",
                "latency_ms": 9.5,
                "auth_enabled": True,
                "error_code": None,
            },
        },
    )
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_list_events",
        lambda **_: {
            "schema_version": "1.0",
            "items": [
                {"request_id": "req-1", "event": "tool.success"},
                {"request_id": "req-1", "event": "tool.success"},
                {"request_id": "req-1", "event": "tool.start"},
            ],
            "page": 1,
            "page_size": 50,
            "total": 3,
            "has_next_page": False,
        },
    )

    response = client.get("/dashboard/fragments/invocation-detail?request_id=req-1")

    assert response.status_code == 200
    assert "Mission Ops / Invocation Detail" in response.text
    assert "Planning" in response.text
    assert "Replayed" in response.text
    assert "Chain mini-map" in response.text


def test_invocation_detail_fragment_uses_conflict_rejected_badge(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_invocation_detail",
        lambda request_id: {
            "schema_version": "1.0",
            "item": {
                "request_id": request_id,
                "recorded_at": "2026-04-02T12:00:00.000+00:00",
                "event": "tool.error",
                "tool": "mars.simulate",
                "correlation_id": "corr-11",
                "mission_id": "mars-alpha",
                "outcome": "failure",
                "latency_ms": 13.0,
                "auth_enabled": True,
                "error_code": "invalid_request",
            },
        },
    )
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_list_events",
        lambda **_: {
            "schema_version": "1.0",
            "items": [{"request_id": "req-conflict", "event": "tool.error"}],
            "page": 1,
            "page_size": 50,
            "total": 1,
            "has_next_page": False,
        },
    )

    response = client.get("/dashboard/fragments/invocation-detail?request_id=req-conflict")

    assert response.status_code == 200
    assert "Conflict-rejected" in response.text


def test_invocation_detail_fragment_empty_state_shape(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_app, "telemetry_invocation_detail", lambda request_id: None)
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_list_events",
        lambda **_: {
            "schema_version": "1.0",
            "items": [],
            "page": 1,
            "page_size": 50,
            "total": 0,
            "has_next_page": False,
        },
    )

    response = client.get("/dashboard/fragments/invocation-detail?request_id=req-missing")

    assert response.status_code == 200
    assert "No invocation found" in response.text
    assert "data-fragment=\"invocation-detail-empty\"" in response.text


def test_correlation_fragment_renders_integrity_warning_for_partial_chain(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_correlation_chain",
        lambda correlation_id: {
            "schema_version": "1.0",
            "correlation_id": correlation_id,
            "items": [
                {
                    "recorded_at": "2026-04-02T10:01:00.000+00:00",
                    "request_id": "req-gov",
                    "event": "tool.success",
                    "tool": "mars.governance",
                    "outcome": "success",
                    "error_code": None,
                }
            ],
        },
    )

    response = client.get("/dashboard/fragments/correlation?correlation_id=corr-partial")

    assert response.status_code == 200
    assert "integrity-partial" in response.text
    assert "Integrity issues:" in response.text
    assert "missing_upstream" in response.text


def test_correlation_fragment_orders_items_deterministically(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_correlation_chain",
        lambda correlation_id: {
            "schema_version": "1.0",
            "correlation_id": correlation_id,
            "items": [
                {
                    "recorded_at": "2026-04-02T10:02:00.000+00:00",
                    "request_id": "req-z",
                    "event": "tool.success",
                    "tool": "mars.benchmark",
                    "outcome": "success",
                    "error_code": None,
                },
                {
                    "recorded_at": "2026-04-02T10:00:00.000+00:00",
                    "request_id": "req-a",
                    "event": "tool.success",
                    "tool": "mars.plan",
                    "outcome": "success",
                    "error_code": None,
                },
            ],
        },
    )

    response = client.get("/dashboard/fragments/correlation?correlation_id=corr-order")

    assert response.status_code == 200
    first = response.text.find("mars.plan")
    second = response.text.find("mars.benchmark")
    assert first != -1
    assert second != -1
    assert first < second


def test_correlation_dag_nodes_present_in_view_model(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full happy-path chain emits four dag-node-present circles."""
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_correlation_chain",
        lambda correlation_id: {
            "schema_version": "1.0",
            "correlation_id": correlation_id,
            "items": [
                {
                    "recorded_at": "2026-04-02T10:00:00.000+00:00",
                    "request_id": "req-1",
                    "event": "tool.success",
                    "tool": "mars.plan",
                    "outcome": "success",
                    "error_code": None,
                },
                {
                    "recorded_at": "2026-04-02T10:01:00.000+00:00",
                    "request_id": "req-2",
                    "event": "tool.success",
                    "tool": "mars.simulate",
                    "outcome": "success",
                    "error_code": None,
                },
                {
                    "recorded_at": "2026-04-02T10:02:00.000+00:00",
                    "request_id": "req-3",
                    "event": "tool.success",
                    "tool": "mars.governance",
                    "outcome": "success",
                    "error_code": None,
                },
                {
                    "recorded_at": "2026-04-02T10:03:00.000+00:00",
                    "request_id": "req-4",
                    "event": "tool.success",
                    "tool": "mars.benchmark",
                    "outcome": "success",
                    "error_code": None,
                },
            ],
        },
    )

    response = client.get("/dashboard/fragments/correlation?correlation_id=corr-full")

    assert response.status_code == 200
    assert response.text.count('dag-node-present') == 4
    assert 'dag-node-missing' not in response.text
    assert 'dag-node-orphaned' not in response.text


def test_correlation_dag_missing_node_detected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate absent but governance present → missing_node issue + dag-node-missing in HTML."""
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_correlation_chain",
        lambda correlation_id: {
            "schema_version": "1.0",
            "correlation_id": correlation_id,
            "items": [
                {
                    "recorded_at": "2026-04-02T10:00:00.000+00:00",
                    "request_id": "req-1",
                    "event": "tool.success",
                    "tool": "mars.plan",
                    "outcome": "success",
                    "error_code": None,
                },
                {
                    "recorded_at": "2026-04-02T10:02:00.000+00:00",
                    "request_id": "req-3",
                    "event": "tool.success",
                    "tool": "mars.governance",
                    "outcome": "success",
                    "error_code": None,
                },
            ],
        },
    )

    response = client.get("/dashboard/fragments/correlation?correlation_id=corr-gap")

    assert response.status_code == 200
    assert "missing_node" in response.text
    assert "dag-node-missing" in response.text
    assert "dag-node-orphaned" in response.text


def test_correlation_dag_cycle_detection(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Governance recorded_at before simulate → cycle issue detected."""
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_correlation_chain",
        lambda correlation_id: {
            "schema_version": "1.0",
            "correlation_id": correlation_id,
            "items": [
                {
                    "recorded_at": "2026-04-02T10:00:00.000+00:00",
                    "request_id": "req-1",
                    "event": "tool.success",
                    "tool": "mars.plan",
                    "outcome": "success",
                    "error_code": None,
                },
                {
                    "recorded_at": "2026-04-02T10:02:00.000+00:00",
                    "request_id": "req-2",
                    "event": "tool.success",
                    "tool": "mars.simulate",
                    "outcome": "success",
                    "error_code": None,
                },
                {
                    "recorded_at": "2026-04-02T10:01:00.000+00:00",
                    "request_id": "req-3",
                    "event": "tool.success",
                    "tool": "mars.governance",
                    "outcome": "success",
                    "error_code": None,
                },
            ],
        },
    )

    response = client.get("/dashboard/fragments/correlation?correlation_id=corr-cycle")

    assert response.status_code == 200
    assert "cycle" in response.text
    assert "integrity-partial" in response.text


def test_build_dag_nodes_includes_latest_outcome() -> None:
    """_build_dag_nodes returns latest_outcome from the last event for that tool."""
    from mars_agent.dashboard_app import _build_dag_nodes

    ordered: list[dict[str, object]] = [
        {
            "recorded_at": "2026-04-07T10:00:00.000+00:00",
            "tool": "mars.plan",
            "outcome": "failure",
        },
        {
            "recorded_at": "2026-04-07T10:01:00.000+00:00",
            "tool": "mars.plan",
            "outcome": "success",
        },
    ]
    nodes = _build_dag_nodes({"mars.plan"}, ordered)
    plan_node = next(n for n in nodes if n["tool"] == "mars.plan")
    assert plan_node["latest_outcome"] == "success"
    simulate_node = next(n for n in nodes if n["tool"] == "mars.simulate")
    assert simulate_node["latest_outcome"] is None


def test_correlation_fragment_unknown_tool_gets_timeline_class(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeline items with unrecognized tools receive the timeline-unknown CSS class."""
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_correlation_chain",
        lambda correlation_id: {
            "schema_version": "1.0",
            "correlation_id": correlation_id,
            "items": [
                {
                    "recorded_at": "2026-04-07T10:00:00.000+00:00",
                    "request_id": "req-u1",
                    "event": "tool.success",
                    "tool": "mars.custom",
                    "outcome": "success",
                    "error_code": None,
                },
            ],
        },
    )

    response = client.get("/dashboard/fragments/correlation?correlation_id=corr-unknown")

    assert response.status_code == 200
    assert "timeline-unknown" in response.text
    assert "unrecognized tools" in response.text

