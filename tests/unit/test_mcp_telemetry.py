from __future__ import annotations

from mars_agent.mcp.telemetry import TELEMETRY_SCHEMA_VERSION, TelemetryQueryService


def _record(
    service: TelemetryQueryService,
    *,
    event: str,
    request_id: str,
    correlation_id: str,
    outcome: str,
    error_code: str | None,
    token: str = "secret-token",
) -> None:
    service.record(
        {
            "event": event,
            "tool": "mars.plan",
            "request_id": request_id,
            "correlation_id": correlation_id,
            "mission_id": "mars-demo",
            "outcome": outcome,
            "latency_ms": 12.5,
            "auth_enabled": True,
            "error_code": error_code,
            "authorization": f"Bearer {token}",
        }
    )


def test_telemetry_list_schema_contract_and_redaction() -> None:
    service = TelemetryQueryService(max_events=20)
    _record(
        service,
        event="tool.error",
        request_id="req-2",
        correlation_id="corr-1",
        outcome="failure",
        error_code="invalid_request",
    )
    _record(
        service,
        event="tool.success",
        request_id="req-1",
        correlation_id="corr-1",
        outcome="success",
        error_code=None,
    )

    response = service.list_events(page=1, page_size=10)
    assert response["schema_version"] == TELEMETRY_SCHEMA_VERSION
    assert response["total"] == 2

    items = response["items"]
    assert isinstance(items, list)
    assert items
    first = items[0]
    assert first["authorization"] == "[REDACTED]"
    assert "recorded_at" in first
    assert "tool" in first
    assert "request_id" in first
    assert "correlation_id" in first
    assert "outcome" in first
    assert "error_code" in first


def test_telemetry_filtering_and_pagination_are_deterministic() -> None:
    service = TelemetryQueryService(max_events=20)
    _record(
        service,
        event="tool.success",
        request_id="req-1",
        correlation_id="corr-a",
        outcome="success",
        error_code=None,
    )
    _record(
        service,
        event="tool.error",
        request_id="req-2",
        correlation_id="corr-a",
        outcome="failure",
        error_code="invalid_request",
    )
    _record(
        service,
        event="tool.error",
        request_id="req-3",
        correlation_id="corr-b",
        outcome="failure",
        error_code="deadline_exceeded",
    )

    failure_page = service.list_events(outcome="failure", page=1, page_size=1)
    assert failure_page["total"] == 2
    assert failure_page["has_next_page"] is True

    next_page = service.list_events(outcome="failure", page=2, page_size=1)
    assert next_page["total"] == 2
    assert next_page["has_next_page"] is False
    assert failure_page["items"][0]["request_id"] != next_page["items"][0]["request_id"]


def test_telemetry_invocation_and_correlation_views() -> None:
    service = TelemetryQueryService(max_events=20)
    _record(
        service,
        event="tool.start",
        request_id="req-z",
        correlation_id="corr-z",
        outcome="started",
        error_code=None,
    )
    _record(
        service,
        event="tool.success",
        request_id="req-z",
        correlation_id="corr-z",
        outcome="success",
        error_code=None,
    )

    detail = service.invocation_detail("req-z")
    assert detail is not None
    assert detail["schema_version"] == TELEMETRY_SCHEMA_VERSION
    assert detail["item"]["request_id"] == "req-z"

    chain = service.correlation_chain("corr-z")
    assert chain["schema_version"] == TELEMETRY_SCHEMA_VERSION
    assert chain["correlation_id"] == "corr-z"
    assert len(chain["items"]) == 2


def test_telemetry_overview_health_and_counts() -> None:
    service = TelemetryQueryService(max_events=20)
    _record(
        service,
        event="tool.success",
        request_id="req-ok",
        correlation_id="corr-ok",
        outcome="success",
        error_code=None,
    )
    _record(
        service,
        event="tool.error",
        request_id="req-fail",
        correlation_id="corr-ok",
        outcome="failure",
        error_code="invalid_request",
    )

    overview = service.overview()
    assert overview["schema_version"] == TELEMETRY_SCHEMA_VERSION
    assert overview["counts"]["events"] == 2
    assert overview["counts"]["successes"] == 1
    assert overview["counts"]["failures"] == 1
    assert overview["health"]["status"] == "degraded"
