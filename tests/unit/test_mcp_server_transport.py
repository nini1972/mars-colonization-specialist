import json
import os
import sys
from datetime import date
from enum import Enum
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult

pytest.importorskip("mcp.server.fastmcp")

from mars_agent.knowledge.models import TrustTier
from mars_agent.mcp.adapter import MarsMCPAdapter
from mars_agent.mcp.server import mars_benchmark, mars_governance, mars_plan, mars_simulate
from mars_agent.orchestration import MissionGoal, MissionPhase
from mars_agent.reasoning.models import EvidenceReference


def _goal() -> MissionGoal:
    return MissionGoal(
        mission_id="mars-phase8-transport",
        crew_size=100,
        horizon_years=5.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=1200.0,
        battery_capacity_kwh=24000.0,
        dust_degradation_fraction=0.2,
        hours_without_sun=20.0,
        desired_confidence=0.9,
    )


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-phase8-001",
            title="Phase 8 MCP transport evidence",
            url="https://example.org/nasa-phase8-001",
            published_on=date(2026, 3, 28),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.97,
        ),
    )


def _goal_payload(goal: MissionGoal) -> dict[str, object]:
    return {
        "mission_id": goal.mission_id,
        "crew_size": goal.crew_size,
        "horizon_years": goal.horizon_years,
        "current_phase": goal.current_phase.value,
        "solar_generation_kw": goal.solar_generation_kw,
        "battery_capacity_kwh": goal.battery_capacity_kwh,
        "dust_degradation_fraction": goal.dust_degradation_fraction,
        "hours_without_sun": goal.hours_without_sun,
        "desired_confidence": goal.desired_confidence,
    }


def _evidence_payload(evidence: tuple[EvidenceReference, ...]) -> list[dict[str, object]]:
    return [
        {
            "source_id": item.source_id,
            "doc_id": item.doc_id,
            "title": item.title,
            "url": item.url,
            "published_on": item.published_on.isoformat(),
            "tier": int(item.tier),
            "relevance_score": item.relevance_score,
        }
        for item in evidence
    ]


def _normalize_payload(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            key: _normalize_payload(item)
            for key, item in value.items()
            if key != "created_at"
        }
    if isinstance(value, list):
        return [_normalize_payload(item) for item in value]
    return value


def _unwrap_success(payload: dict[str, object]) -> dict[str, object]:
    assert payload["ok"] is True
    assert isinstance(payload["request_id"], str)
    return {
        key: value
        for key, value in payload.items()
        if key not in {"ok", "request_id"}
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _server_env() -> dict[str, str]:
    env = dict(os.environ)
    src_path = str(_repo_root() / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src_path if not existing else f"{src_path}{os.pathsep}{existing}"
    return env


async def _call_tool_stdio(
    name: str,
    arguments: dict[str, object],
    meta: dict[str, object] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> CallToolResult:
    env = _server_env()
    if env_overrides is not None:
        env.update(env_overrides)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mars_agent.mcp"],
        env=env,
        cwd=_repo_root(),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            meta_payload = meta if meta is None else dict(meta)
            return await session.call_tool(name, arguments, meta=meta_payload)


def _call_tool_stdio_sync(
    name: str,
    arguments: dict[str, object],
    meta: dict[str, object] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> CallToolResult:
    async def _runner() -> CallToolResult:
        return await _call_tool_stdio(name, arguments, meta=meta, env_overrides=env_overrides)

    return anyio.run(_runner)


def _tool_error_text(result: CallToolResult) -> str:
    return "\n".join(
        block.text
        for block in result.content
        if getattr(block, "type", None) == "text" and hasattr(block, "text")
    )


def _parse_error_payload(error_text: str) -> dict[str, object]:
    start = error_text.find("{")
    assert start >= 0
    payload = json.loads(error_text[start:])
    assert isinstance(payload, dict)
    return payload


def test_transport_plan_matches_adapter_invoke_output() -> None:
    goal = _goal()
    evidence = _evidence()

    adapter = MarsMCPAdapter()
    expected = adapter.invoke(
        "mars.plan",
        {
            "goal": _goal_payload(goal),
            "evidence": _evidence_payload(evidence),
        },
    )

    actual = mars_plan(
        goal=_goal_payload(goal),
        evidence=_evidence_payload(evidence),
        request_id="req-direct-plan",
    )

    assert actual["request_id"] == "req-direct-plan"
    assert _normalize_payload(_unwrap_success(actual)) == _normalize_payload(expected)


def test_transport_end_to_end_pipeline_matches_adapter_invoke_output() -> None:
    goal = _goal()
    evidence = _evidence()

    adapter = MarsMCPAdapter()
    expected_plan = adapter.invoke(
        "mars.plan",
        {
            "goal": _goal_payload(goal),
            "evidence": _evidence_payload(evidence),
        },
    )
    expected_plan_id = expected_plan["plan_id"]
    assert isinstance(expected_plan_id, str)

    expected_simulation = adapter.invoke(
        "mars.simulate",
        {
            "plan_id": expected_plan_id,
            "seed": 42,
            "max_repair_attempts": 2,
        },
    )
    expected_simulation_id = expected_simulation["simulation_id"]
    assert isinstance(expected_simulation_id, str)

    expected_governance = adapter.invoke(
        "mars.governance",
        {
            "plan_id": expected_plan_id,
            "simulation_id": expected_simulation_id,
            "min_confidence": 0.75,
        },
    )
    expected_benchmark = adapter.invoke(
        "mars.benchmark",
        {
            "plan_id": expected_plan_id,
            "simulation_id": expected_simulation_id,
        },
    )

    actual_plan = mars_plan(
        goal=_goal_payload(goal),
        evidence=_evidence_payload(evidence),
        request_id="req-direct-e2e-plan",
    )
    assert actual_plan["request_id"] == "req-direct-e2e-plan"
    plan_payload = _unwrap_success(actual_plan)
    actual_plan_id = plan_payload["plan_id"]
    assert isinstance(actual_plan_id, str)

    actual_simulation = mars_simulate(
        plan_id=actual_plan_id,
        seed=42,
        max_repair_attempts=2,
        request_id="req-direct-e2e-sim",
    )
    assert actual_simulation["request_id"] == "req-direct-e2e-sim"
    simulation_payload = _unwrap_success(actual_simulation)
    actual_simulation_id = simulation_payload["simulation_id"]
    assert isinstance(actual_simulation_id, str)

    actual_governance = mars_governance(
        plan_id=actual_plan_id,
        simulation_id=actual_simulation_id,
        min_confidence=0.75,
        request_id="req-direct-e2e-gov",
    )
    actual_benchmark = mars_benchmark(
        plan_id=actual_plan_id,
        simulation_id=actual_simulation_id,
        request_id="req-direct-e2e-bench",
    )

    assert _normalize_payload(simulation_payload["simulation"]) == _normalize_payload(
        expected_simulation["simulation"]
    )
    assert actual_governance["request_id"] == "req-direct-e2e-gov"
    assert actual_benchmark["request_id"] == "req-direct-e2e-bench"
    assert _normalize_payload(_unwrap_success(actual_governance)) == _normalize_payload(
        expected_governance
    )
    assert _normalize_payload(_unwrap_success(actual_benchmark)) == _normalize_payload(
        expected_benchmark
    )


def test_transport_stdio_call_matches_adapter_and_emits_request_id() -> None:
    goal = _goal()
    evidence = _evidence()

    adapter = MarsMCPAdapter()
    expected = adapter.invoke(
        "mars.plan",
        {
            "goal": _goal_payload(goal),
            "evidence": _evidence_payload(evidence),
        },
    )

    stdio_arguments: dict[str, object] = {
        "goal": _goal_payload(goal),
        "evidence": _evidence_payload(evidence),
        "request_id": "req-stdio-plan",
    }

    actual = _call_tool_stdio_sync("mars.plan", stdio_arguments)

    structured = actual.structuredContent
    assert isinstance(structured, dict)
    assert structured["request_id"] == "req-stdio-plan"
    assert _normalize_payload(_unwrap_success(structured)) == _normalize_payload(expected)


def test_transport_stdio_error_uses_mcp_error_semantics() -> None:
    stdio_arguments: dict[str, object] = {
        "plan_id": "missing-plan",
        "request_id": "req-stdio-error",
    }

    result = _call_tool_stdio_sync("mars.simulate", stdio_arguments)

    assert result.isError is True
    error_text = _tool_error_text(result)
    error_payload = _parse_error_payload(error_text)
    assert error_payload["schema_version"] == "1.0"
    assert error_payload["tool"] == "mars.simulate"
    assert error_payload["code"] == "invalid_request"
    assert error_payload["request_id"] == "req-stdio-error"
    assert isinstance(error_payload["message"], str)


def test_transport_stdio_auth_missing_token_returns_unauthenticated() -> None:
    result = _call_tool_stdio_sync(
        "mars.plan",
        {
            "goal": _goal_payload(_goal()),
            "evidence": _evidence_payload(_evidence()),
            "request_id": "req-auth-missing",
        },
        env_overrides={
            "MARS_MCP_AUTH_ENABLED": "true",
            "MARS_MCP_AUTH_TOKEN": "secret-token",
        },
    )

    assert result.isError is True
    error_payload = _parse_error_payload(_tool_error_text(result))
    assert error_payload["schema_version"] == "1.0"
    assert error_payload["tool"] == "mars.plan"
    assert error_payload["code"] == "unauthenticated"
    assert error_payload["request_id"] == "req-auth-missing"


def test_transport_stdio_auth_invalid_token_returns_unauthenticated() -> None:
    result = _call_tool_stdio_sync(
        "mars.plan",
        {
            "goal": _goal_payload(_goal()),
            "evidence": _evidence_payload(_evidence()),
            "request_id": "req-auth-invalid",
        },
        meta={"authorization": "Bearer wrong-token"},
        env_overrides={
            "MARS_MCP_AUTH_ENABLED": "true",
            "MARS_MCP_AUTH_TOKEN": "secret-token",
        },
    )

    assert result.isError is True
    error_payload = _parse_error_payload(_tool_error_text(result))
    assert error_payload["schema_version"] == "1.0"
    assert error_payload["tool"] == "mars.plan"
    assert error_payload["code"] == "unauthenticated"
    assert error_payload["request_id"] == "req-auth-invalid"


def test_transport_stdio_auth_insufficient_permission_returns_forbidden() -> None:
    result = _call_tool_stdio_sync(
        "mars.simulate",
        {
            "plan_id": "missing-plan",
            "request_id": "req-auth-forbidden",
        },
        meta={"authorization": "Bearer secret-token"},
        env_overrides={
            "MARS_MCP_AUTH_ENABLED": "true",
            "MARS_MCP_AUTH_TOKEN": "secret-token",
            "MARS_MCP_AUTH_PERMISSIONS": "plan",
        },
    )

    assert result.isError is True
    error_payload = _parse_error_payload(_tool_error_text(result))
    assert error_payload["schema_version"] == "1.0"
    assert error_payload["tool"] == "mars.simulate"
    assert error_payload["code"] == "forbidden"
    assert error_payload["request_id"] == "req-auth-forbidden"


def test_transport_stdio_auth_valid_token_and_permission_preserves_success_shape() -> None:
    goal = _goal()
    evidence = _evidence()
    adapter = MarsMCPAdapter()
    expected = adapter.invoke(
        "mars.plan",
        {
            "goal": _goal_payload(goal),
            "evidence": _evidence_payload(evidence),
        },
    )

    result = _call_tool_stdio_sync(
        "mars.plan",
        {
            "goal": _goal_payload(goal),
            "evidence": _evidence_payload(evidence),
            "request_id": "req-auth-ok",
        },
        meta={"authorization": "Bearer secret-token"},
        env_overrides={
            "MARS_MCP_AUTH_ENABLED": "true",
            "MARS_MCP_AUTH_TOKEN": "secret-token",
            "MARS_MCP_AUTH_PERMISSIONS": "plan",
        },
    )

    assert result.isError is False
    structured = result.structuredContent
    assert isinstance(structured, dict)
    assert structured["request_id"] == "req-auth-ok"
    assert _normalize_payload(_unwrap_success(structured)) == _normalize_payload(expected)
