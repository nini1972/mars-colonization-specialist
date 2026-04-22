from __future__ import annotations

import json
from pathlib import Path

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


def _patch_plan_runtime_metrics(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sync_calls: float = 0.0,
    async_calls: float = 0.0,
) -> None:
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_plan_runtime_metrics",
        lambda: {"sync_calls": sync_calls, "async_calls": async_calls},
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


def _patch_negotiations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_negotiation_sessions",
        lambda limit=5: {
            "schema_version": "1.0",
            "items": [
                {
                    "recorded_at": "2026-04-22T12:00:00.000+00:00",
                    "session_id": "neg-abc123",
                    "round_id": "round-0",
                    "mission_id": "mars-demo",
                    "current_phase": "early_operations",
                    "current_reduction": 0.0,
                    "conflict_ids": ["coupling.power_balance.shortfall"],
                    "decision": {
                        "source": "fallback",
                        "is_fallback": True,
                        "rationale": "Conflict-aware fallback",
                    },
                    "proposals": [
                        {
                            "subsystem": "isru",
                            "knob_name": "isru_reduction_fraction",
                            "suggested_delta": 0.05,
                            "rationale": "Trim ISRU load first.",
                        }
                    ],
                    "counter_proposals": [
                        {
                            "reviewer_subsystem": "power",
                            "proposal_subsystem": "isru",
                            "knob_name": "isru_reduction_fraction",
                            "suggested_delta": 0.1,
                            "rationale": "Power needs a deeper trim.",
                        }
                    ],
                    "messages": [
                        {
                            "sequence": 0,
                            "sender": "planner",
                            "kind": "session_started",
                            "recipients": [],
                        },
                        {
                            "sequence": 1,
                            "sender": "isru",
                            "kind": "proposal_submitted",
                            "recipients": ["planner"],
                        },
                    ],
                }
            ],
        },
    )


def _patch_benchmark_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dashboard_app,
        "list_policy_profiles",
        lambda: (
            {
                "name": "nasa-esa-mission-review",
                "policy_version": "2026.03",
                "policy_source": "NASA/ESA mission review benchmark bundle",
                "is_default": True,
                "metrics": ["load_margin_kw", "resource_surplus_ratio"],
                "reference_count": 2,
            },
            {
                "name": "nasa-esa-mission-review-permissive",
                "policy_version": "2026.03-dev",
                "policy_source": "Synthetic permissive mission review benchmark bundle",
                "is_default": False,
                "metrics": ["load_margin_kw", "resource_surplus_ratio", "storage_cover_hours"],
                "reference_count": 3,
            },
        ),
    )


def test_dashboard_main_page_exposes_ui_schema_version(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)
    _patch_plan_runtime_metrics(monkeypatch)
    _patch_negotiations(monkeypatch)
    _patch_benchmark_profiles(monkeypatch)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "UI schema version" in response.text
    assert dashboard_app.UI_SCHEMA_VERSION in response.text


def test_dashboard_json_endpoint_keeps_json_contract(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)
    _patch_plan_runtime_metrics(monkeypatch)
    _patch_negotiations(monkeypatch)
    _patch_benchmark_profiles(monkeypatch)

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
    _patch_plan_runtime_metrics(monkeypatch)

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
    _patch_plan_runtime_metrics(monkeypatch)

    response = client.get("/dashboard/fragments/replay")

    assert response.status_code == 200
    assert "degraded" in response.text.lower()


def test_dashboard_page_includes_loading_skeleton_markers(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)
    _patch_plan_runtime_metrics(monkeypatch)
    _patch_negotiations(monkeypatch)
    _patch_benchmark_profiles(monkeypatch)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Loading correlation chain" in response.text
    assert "Loading invocation details" in response.text
    assert "Refreshing replay indicators" in response.text
    assert "Loading benchmark profile catalog" in response.text


def test_main_page_shows_degraded_banner_when_backend_is_degraded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch, degraded=True)
    _patch_plan_runtime_metrics(monkeypatch)
    _patch_negotiations(monkeypatch)
    _patch_benchmark_profiles(monkeypatch)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Persistence is degraded" in response.text


def test_fragment_integrity_markers_exist_for_all_fragment_routes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)
    _patch_plan_runtime_metrics(monkeypatch)
    _patch_invocations(monkeypatch)
    _patch_benchmark_profiles(monkeypatch)
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
    benchmark_profiles = client.get("/dashboard/fragments/benchmark-profiles")

    assert overview.status_code == 200
    assert invocations.status_code == 200
    assert correlation.status_code == 200
    assert replay.status_code == 200
    assert benchmark_profiles.status_code == 200

    for payload in (
        overview.text,
        invocations.text,
        correlation.text,
        replay.text,
        benchmark_profiles.text,
    ):
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
    _patch_plan_runtime_metrics(monkeypatch)
    _patch_negotiations(monkeypatch)
    _patch_benchmark_profiles(monkeypatch)

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


def test_negotiations_fragment_renders_transcript_and_proposals(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_negotiations(monkeypatch)

    response = client.get("/dashboard/fragments/negotiations")

    assert response.status_code == 200
    assert "Negotiation Sessions" in response.text
    assert "neg-abc123" in response.text
    assert "Specialist Proposals" in response.text
    assert "Counter-Proposals" in response.text
    assert "proposal_submitted" in response.text


def test_dashboard_page_includes_negotiation_panel_loading_marker(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)
    _patch_plan_runtime_metrics(monkeypatch)
    _patch_negotiations(monkeypatch)
    _patch_benchmark_profiles(monkeypatch)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Loading negotiation sessions" in response.text
    assert "id=\"negotiations-panel\"" in response.text


def test_benchmark_profiles_fragment_renders_visual_catalog(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_benchmark_profiles(monkeypatch)

    response = client.get("/dashboard/fragments/benchmark-profiles")

    assert response.status_code == 200
    assert "Benchmark Profile Catalog" in response.text
    assert "nasa-esa-mission-review" in response.text
    assert "nasa-esa-mission-review-permissive" in response.text
    assert "load_margin_kw" in response.text
    assert "default" in response.text


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


def test_mcp_http_endpoint_is_mounted() -> None:
    """The /mcp sub-app must be registered on the FastAPI app at startup."""
    from starlette.routing import Mount

    mounted_paths = [
        route.path
        for route in dashboard_app.app.routes
        if isinstance(route, Mount)
    ]
    assert "/mcp" in mounted_paths


def test_overview_fragment_renders_plan_runtime_counters(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)
    _patch_plan_runtime_metrics(monkeypatch, sync_calls=12.0, async_calls=4.0)

    response = client.get("/dashboard/fragments/overview")

    assert response.status_code == 200
    assert "Plan Sync Calls" in response.text
    assert "Plan Async Calls" in response.text
    assert "Plan Runtime Ratio" in response.text
    assert ">12.0<" in response.text
    assert ">4.0<" in response.text
    assert "75.0% / 25.0%" in response.text


def test_overview_fragment_runtime_ratio_handles_zero_calls(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)
    _patch_plan_runtime_metrics(monkeypatch, sync_calls=0.0, async_calls=0.0)

    response = client.get("/dashboard/fragments/overview")

    assert response.status_code == 200
    assert "Plan Runtime Ratio" in response.text
    assert "0.0% / 0.0%" in response.text


def test_overview_fragment_rejects_invalid_plan_runtime_shape(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_dashboard_snapshot(monkeypatch)
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_plan_runtime_metrics",
        lambda: {"sync_calls": 1.0},
    )

    response = client.get("/dashboard/fragments/overview")

    assert response.status_code == 500
    assert "plan_runtime" in response.text


# ---------------------------------------------------------------------------
# Track E — Agent Health Panel
# ---------------------------------------------------------------------------


def _patch_specialist_metrics(
    monkeypatch: pytest.MonkeyPatch,
    data: dict[str, dict[str, float]] | None = None,
) -> None:
    monkeypatch.setattr(
        dashboard_app,
        "telemetry_specialist_metrics",
        lambda: data if data is not None else {},
    )


def test_agents_fragment_returns_200(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_specialist_metrics(monkeypatch)
    response = client.get("/dashboard/fragments/agents")
    assert response.status_code == 200


def test_agents_fragment_has_data_fragment(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_specialist_metrics(monkeypatch)
    response = client.get("/dashboard/fragments/agents")
    assert response.status_code == 200
    assert 'data-fragment="agents"' in response.text


def test_agents_empty_state_when_no_metrics(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_specialist_metrics(monkeypatch, data={})
    response = client.get("/dashboard/fragments/agents")
    assert response.status_code == 200
    assert "No specialist metrics recorded yet" in response.text


def test_agents_fragment_shows_eclss_when_data_present(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_specialist_metrics(
        monkeypatch,
        data={
            "specialist.eclss": {
                "calls": 2.0,
                "total_latency_ms": 40.0,
                "gate_pass": 2.0,
                "gate_fail": 0.0,
                "last_gate_accepted": 1.0,
                "last_latency_ms": 18.0,
            }
        },
    )
    response = client.get("/dashboard/fragments/agents")
    assert response.status_code == 200
    assert "Eclss" in response.text


def test_agents_fragment_required_keys_present(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_specialist_metrics(monkeypatch)
    response = client.get("/dashboard/fragments/agents")
    assert response.status_code == 200
    assert 'data-fragment-required="agents,ui_schema_version"' in response.text


# ---------------------------------------------------------------------------
# Planner soak overview panel
# ---------------------------------------------------------------------------


def test_soak_overview_fragment_unavailable_without_artifact(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dashboard_app, "_DEFAULT_SOAK_REPORT_CANDIDATES", tuple())
    monkeypatch.delenv("MARS_DASHBOARD_SOAK_REPORT_PATH", raising=False)

    response = client.get("/dashboard/fragments/soak-overview")

    assert response.status_code == 200
    assert "No soak report artifact found yet" in response.text


def test_soak_overview_fragment_renders_table_when_artifact_present(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    soak_path = tmp_path / "planner-soak-ci.json"
    soak_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "config": {
                    "mode": "both",
                    "requests": 240,
                    "concurrency": 24,
                    "timeout_seconds": 20.0,
                    "persistence_backend": "sqlite",
                    "sqlite_path": ".mars_mcp_runtime.soak.sqlite3",
                    "reset_runtime_state": True,
                },
                "results": [
                    {
                        "mode": "sync",
                        "requests": 240,
                        "concurrency": 24,
                        "elapsed_seconds": 2.5,
                        "throughput_rps": 96.0,
                        "successes": 240,
                        "failures": 0,
                        "error_rate": 0.0,
                        "degraded_count": 0,
                        "degraded_rate": 0.0,
                        "latency_ms": {
                            "avg": 14.0,
                            "p50": 12.0,
                            "p95": 23.0,
                            "p99": 29.0,
                            "max": 32.0,
                        },
                        "runtime_counter_delta": {"sync_calls": 240.0, "async_calls": 0.0},
                        "error_types": {},
                    },
                    {
                        "mode": "async",
                        "requests": 240,
                        "concurrency": 24,
                        "elapsed_seconds": 2.4,
                        "throughput_rps": 100.0,
                        "successes": 240,
                        "failures": 0,
                        "error_rate": 0.0,
                        "degraded_count": 0,
                        "degraded_rate": 0.0,
                        "latency_ms": {
                            "avg": 13.0,
                            "p50": 11.0,
                            "p95": 21.0,
                            "p99": 27.0,
                            "max": 30.0,
                        },
                        "runtime_counter_delta": {"sync_calls": 0.0, "async_calls": 240.0},
                        "error_types": {},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MARS_DASHBOARD_SOAK_REPORT_PATH", str(soak_path))

    response = client.get("/dashboard/fragments/soak-overview")

    assert response.status_code == 200
    assert "Planner Soak Overview" in response.text
    assert ">sync<" in response.text
    assert ">async<" in response.text
    assert ">96.0<" in response.text
    assert ">100.0<" in response.text
    assert "Updated:" in response.text
    assert "toLocaleString" in response.text


def test_soak_overview_fragment_invalid_payload_shows_warning(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bad_path = tmp_path / "invalid-soak.json"
    bad_path.write_text('{"not":"valid"}', encoding="utf-8")
    monkeypatch.setenv("MARS_DASHBOARD_SOAK_REPORT_PATH", str(bad_path))

    response = client.get("/dashboard/fragments/soak-overview")

    assert response.status_code == 200
    assert "invalid" in response.text.lower()


# ---------------------------------------------------------------------------
# Gap #7 — Fault rendering in Agent Health panel
# ---------------------------------------------------------------------------


def test_agents_fragment_shows_faults_column(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agents fragment must include a Faults column header."""
    _patch_specialist_metrics(
        monkeypatch,
        data={
            "specialist.eclss": {
                "calls": 1.0,
                "total_latency_ms": 5.0,
                "gate_pass": 1.0,
                "gate_fail": 0.0,
                "last_gate_accepted": 1.0,
                "last_latency_ms": 5.0,
                "fault_count": 0.0,
                "last_failed": 0.0,
            }
        },
    )
    response = client.get("/dashboard/fragments/agents")
    assert response.status_code == 200
    assert "Faults" in response.text


def test_agents_fragment_shows_gate_reject_label(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agents fragment must label gate rejections distinctly from runtime faults."""
    _patch_specialist_metrics(
        monkeypatch,
        data={
            "specialist.eclss": {
                "calls": 1.0,
                "total_latency_ms": 5.0,
                "gate_pass": 0.0,
                "gate_fail": 1.0,
                "last_gate_accepted": 0.0,
                "last_latency_ms": 5.0,
                "fault_count": 0.0,
                "last_failed": 0.0,
            }
        },
    )
    response = client.get("/dashboard/fragments/agents")
    assert response.status_code == 200
    assert "Gate Reject" in response.text


def test_agents_fragment_shows_failure_formula_note(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agents fragment should explain how total failures are composed."""
    _patch_specialist_metrics(
        monkeypatch,
        data={
            "specialist.power": {
                "calls": 1.0,
                "total_latency_ms": 0.0,
                "gate_pass": 0.0,
                "gate_fail": 0.0,
                "last_gate_accepted": 0.0,
                "last_latency_ms": 0.0,
                "fault_count": 1.0,
                "last_failed": 1.0,
            }
        },
    )
    response = client.get("/dashboard/fragments/agents")
    assert response.status_code == 200
    assert "Total failures = Gate Reject + Faults" in response.text


def test_agents_fragment_shows_fault_count_when_faults_present(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agents fragment must surface fault_count when a specialist has recorded faults."""
    _patch_specialist_metrics(
        monkeypatch,
        data={
            "specialist.isru": {
                "calls": 3.0,
                "total_latency_ms": 30.0,
                "gate_pass": 2.0,
                "gate_fail": 0.0,
                "last_gate_accepted": 0.0,
                "last_latency_ms": 8.0,
                "fault_count": 1.0,
                "last_failed": 0.0,
            }
        },
    )
    response = client.get("/dashboard/fragments/agents")
    assert response.status_code == 200
    assert "Isru" in response.text
    assert "1" in response.text  # fault_count


def test_agents_fragment_shows_warn_status_when_last_failed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When last_failed=1 the agent row must render the status-warn CSS class."""
    _patch_specialist_metrics(
        monkeypatch,
        data={
            "specialist.power": {
                "calls": 2.0,
                "total_latency_ms": 20.0,
                "gate_pass": 1.0,
                "gate_fail": 0.0,
                "last_gate_accepted": 0.0,
                "last_latency_ms": 10.0,
                "fault_count": 1.0,
                "last_failed": 1.0,
            }
        },
    )
    response = client.get("/dashboard/fragments/agents")
    assert response.status_code == 200
    assert "status-warn" in response.text
    assert "fault" in response.text


def test_agents_fragment_no_warn_status_when_healthy(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Healthy specialist rows must not render the status-warn class."""
    _patch_specialist_metrics(
        monkeypatch,
        data={
            "specialist.eclss": {
                "calls": 2.0,
                "total_latency_ms": 10.0,
                "gate_pass": 2.0,
                "gate_fail": 0.0,
                "last_gate_accepted": 1.0,
                "last_latency_ms": 4.0,
                "fault_count": 0.0,
                "last_failed": 0.0,
            }
        },
    )
    response = client.get("/dashboard/fragments/agents")
    assert response.status_code == 200
    assert "status-warn" not in response.text
    assert "status-pass" in response.text

