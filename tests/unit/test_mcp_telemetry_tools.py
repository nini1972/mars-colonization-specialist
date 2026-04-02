from __future__ import annotations

import anyio
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mars_agent.mcp.server import (
    mars_plan,
    mars_telemetry_correlation,
    mars_telemetry_dashboard,
    mars_telemetry_events,
    mars_telemetry_invocation,
    mars_telemetry_overview,
)
from tests.unit.test_mcp_server_transport import _evidence, _evidence_payload, _goal, _goal_payload


def _call_direct_sync(coro):
    async def _runner():
        return await coro

    return anyio.run(_runner)


def _parse_tool_error(exc: ToolError) -> dict[str, object]:
    text = str(exc)
    start = text.find("{")
    payload = text[start:] if start >= 0 else text
    import json

    decoded = json.loads(payload)
    assert isinstance(decoded, dict)
    return decoded


def test_telemetry_dashboard_tool_returns_week1_panels() -> None:
    _call_direct_sync(
        mars_plan(
            goal=_goal_payload(_goal()),
            evidence=_evidence_payload(_evidence()),
            request_id="req-telemetry-dashboard-seed",
        )
    )

    result = _call_direct_sync(
        mars_telemetry_dashboard(
            page_size=10,
            request_id="req-telemetry-dashboard",
        )
    )

    assert result["ok"] is True
    dashboard = result["dashboard"]
    assert isinstance(dashboard, dict)
    assert "overview_panel" in dashboard
    assert "invocation_panel" in dashboard
    assert "correlation_panel" in dashboard
    assert "replay_degraded_panel" in dashboard


def test_telemetry_tools_support_invocation_and_correlation_views() -> None:
    plan = _call_direct_sync(
        mars_plan(
            goal=_goal_payload(_goal()),
            evidence=_evidence_payload(_evidence()),
            request_id="req-telemetry-plan-flow",
        )
    )
    assert plan["ok"] is True

    overview = _call_direct_sync(
        mars_telemetry_overview(request_id="req-telemetry-overview")
    )
    assert overview["ok"] is True

    events = _call_direct_sync(
        mars_telemetry_events(
            tool="mars.plan",
            page=1,
            page_size=20,
            request_id="req-telemetry-events",
        )
    )
    assert events["ok"] is True

    invocation = _call_direct_sync(
        mars_telemetry_invocation(
            invocation_request_id="req-telemetry-plan-flow",
            request_id="req-telemetry-invocation",
        )
    )
    assert invocation["ok"] is True

    correlation = _call_direct_sync(
        mars_telemetry_correlation(
            correlation_id="req-telemetry-plan-flow",
            request_id="req-telemetry-correlation",
        )
    )
    assert correlation["ok"] is True


def test_telemetry_tools_require_telemetry_permission_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("MARS_MCP_AUTH_TOKEN", "secret-token")
    monkeypatch.setenv("MARS_MCP_AUTH_PERMISSIONS", "plan")

    with pytest.raises(ToolError) as exc_info:
        _call_direct_sync(
            mars_telemetry_overview(
                request_id="req-telemetry-authz",
                auth_token="secret-token",
            )
        )

    error = _parse_tool_error(exc_info.value)
    assert error["code"] == "forbidden"
