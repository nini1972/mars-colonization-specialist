import json
import os
import sys
from collections.abc import Coroutine
from datetime import date
from enum import Enum
from pathlib import Path
from typing import cast

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, LoggingMessageNotificationParams

pytest.importorskip("mcp.server.fastmcp")

from mars_agent.knowledge.models import TrustTier
from mars_agent.mcp import server as mcp_server
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


def _call_direct_sync(coro: Coroutine[object, object, dict[str, object]]) -> dict[str, object]:
    async def _runner() -> dict[str, object]:
        return await coro

    return anyio.run(_runner)


async def _run_stdio_pipeline(
    goal_payload: dict[str, object],
    evidence_payload: list[dict[str, object]],
    env_overrides: dict[str, str] | None = None,
) -> tuple[CallToolResult, CallToolResult, CallToolResult, CallToolResult]:
    """Run all 4 tools sequentially in a single server process and return their results."""
    env = _server_env()
    if env_overrides:
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

            plan_result = await session.call_tool(
                "mars.plan",
                {
                    "goal": goal_payload,
                    "evidence": evidence_payload,
                    "request_id": "req-parity-plan",
                },
            )
            plan_content = plan_result.structuredContent
            assert isinstance(plan_content, dict), "mars.plan must return structured content"
            plan_id = plan_content["plan_id"]
            assert isinstance(plan_id, str)

            sim_result = await session.call_tool(
                "mars.simulate",
                {
                    "plan_id": plan_id,
                    "seed": 42,
                    "max_repair_attempts": 2,
                    "request_id": "req-parity-sim",
                },
            )
            sim_content = sim_result.structuredContent
            assert isinstance(sim_content, dict), "mars.simulate must return structured content"
            sim_id = sim_content["simulation_id"]
            assert isinstance(sim_id, str)

            gov_result = await session.call_tool(
                "mars.governance",
                {
                    "plan_id": plan_id,
                    "simulation_id": sim_id,
                    "min_confidence": 0.75,
                    "request_id": "req-parity-gov",
                },
            )
            bench_result = await session.call_tool(
                "mars.benchmark",
                {
                    "plan_id": plan_id,
                    "simulation_id": sim_id,
                    "request_id": "req-parity-bench",
                },
            )

            return plan_result, sim_result, gov_result, bench_result


async def _run_stdio_pipeline_with_observability(
    goal_payload: dict[str, object],
    evidence_payload: list[dict[str, object]],
    env_overrides: dict[str, str] | None = None,
) -> tuple[
    tuple[CallToolResult, CallToolResult, CallToolResult, CallToolResult],
    list[tuple[float, float | None, str | None]],
    list[str],
]:
    env = _server_env()
    if env_overrides:
        env.update(env_overrides)

    progress_events: list[tuple[float, float | None, str | None]] = []
    log_messages: list[str] = []

    async def _on_progress(progress: float, total: float | None, message: str | None) -> None:
        progress_events.append((progress, total, message))

    async def _on_log(params: LoggingMessageNotificationParams) -> None:
        data = params.data
        log_messages.append(data if isinstance(data, str) else str(data))

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mars_agent.mcp"],
        env=env,
        cwd=_repo_root(),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            logging_callback=_on_log,
        ) as session:
            await session.initialize()

            plan_result = await session.call_tool(
                "mars.plan",
                {
                    "goal": goal_payload,
                    "evidence": evidence_payload,
                    "request_id": "req-observe-plan",
                },
                progress_callback=_on_progress,
            )
            plan_content = plan_result.structuredContent
            assert isinstance(plan_content, dict)
            plan_id = plan_content["plan_id"]
            assert isinstance(plan_id, str)

            sim_result = await session.call_tool(
                "mars.simulate",
                {
                    "plan_id": plan_id,
                    "seed": 42,
                    "max_repair_attempts": 2,
                    "request_id": "req-observe-sim",
                },
                progress_callback=_on_progress,
            )
            sim_content = sim_result.structuredContent
            assert isinstance(sim_content, dict)
            sim_id = sim_content["simulation_id"]
            assert isinstance(sim_id, str)

            gov_result = await session.call_tool(
                "mars.governance",
                {
                    "plan_id": plan_id,
                    "simulation_id": sim_id,
                    "min_confidence": 0.75,
                    "request_id": "req-observe-gov",
                },
                progress_callback=_on_progress,
            )
            bench_result = await session.call_tool(
                "mars.benchmark",
                {
                    "plan_id": plan_id,
                    "simulation_id": sim_id,
                    "request_id": "req-observe-bench",
                },
                progress_callback=_on_progress,
            )

            return (
                (plan_result, sim_result, gov_result, bench_result),
                progress_events,
                log_messages,
            )


def _run_stdio_pipeline_sync(
    goal_payload: dict[str, object],
    evidence_payload: list[dict[str, object]],
    env_overrides: dict[str, str] | None = None,
) -> tuple[CallToolResult, CallToolResult, CallToolResult, CallToolResult]:
    async def _runner() -> tuple[CallToolResult, CallToolResult, CallToolResult, CallToolResult]:
        return await _run_stdio_pipeline(goal_payload, evidence_payload, env_overrides)

    return anyio.run(_runner)


def _run_stdio_pipeline_with_observability_sync(
    goal_payload: dict[str, object],
    evidence_payload: list[dict[str, object]],
    env_overrides: dict[str, str] | None = None,
) -> tuple[
    tuple[CallToolResult, CallToolResult, CallToolResult, CallToolResult],
    list[tuple[float, float | None, str | None]],
    list[str],
]:
    async def _runner() -> tuple[
        tuple[CallToolResult, CallToolResult, CallToolResult, CallToolResult],
        list[tuple[float, float | None, str | None]],
        list[str],
    ]:
        return await _run_stdio_pipeline_with_observability(
            goal_payload,
            evidence_payload,
            env_overrides,
        )

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


def _parse_tool_error(exc: ToolError) -> dict[str, object]:
    return _parse_error_payload(str(exc))


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

    actual = _call_direct_sync(
        mars_plan(
            goal=_goal_payload(goal),
            evidence=_evidence_payload(evidence),
            request_id="req-direct-plan",
        )
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

    actual_plan = _call_direct_sync(
        mars_plan(
            goal=_goal_payload(goal),
            evidence=_evidence_payload(evidence),
            request_id="req-direct-e2e-plan",
        )
    )
    assert actual_plan["request_id"] == "req-direct-e2e-plan"
    plan_payload = _unwrap_success(actual_plan)
    actual_plan_id = plan_payload["plan_id"]
    assert isinstance(actual_plan_id, str)

    actual_simulation = _call_direct_sync(
        mars_simulate(
            plan_id=actual_plan_id,
            seed=42,
            max_repair_attempts=2,
            request_id="req-direct-e2e-sim",
        )
    )
    assert actual_simulation["request_id"] == "req-direct-e2e-sim"
    simulation_payload = _unwrap_success(actual_simulation)
    actual_simulation_id = simulation_payload["simulation_id"]
    assert isinstance(actual_simulation_id, str)

    actual_governance = _call_direct_sync(
        mars_governance(
            plan_id=actual_plan_id,
            simulation_id=actual_simulation_id,
            min_confidence=0.75,
            request_id="req-direct-e2e-gov",
        )
    )
    actual_benchmark = _call_direct_sync(
        mars_benchmark(
            plan_id=actual_plan_id,
            simulation_id=actual_simulation_id,
            request_id="req-direct-e2e-bench",
        )
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


def test_transport_stdio_semantic_validation_fails_fast_with_stable_error() -> None:
    invalid_goal = _goal_payload(_goal())
    invalid_goal["desired_confidence"] = 1.5

    result = _call_tool_stdio_sync(
        "mars.plan",
        {
            "goal": invalid_goal,
            "evidence": _evidence_payload(_evidence()),
            "request_id": "req-stdio-invalid-goal",
        },
    )

    assert result.isError is True
    error_payload = _parse_error_payload(_tool_error_text(result))
    assert error_payload["schema_version"] == "1.0"
    assert error_payload["tool"] == "mars.plan"
    assert error_payload["code"] == "invalid_request"
    assert error_payload["request_id"] == "req-stdio-invalid-goal"
    assert "desired_confidence" in str(error_payload["message"])


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


def test_transport_direct_duplicate_request_id_replays_completed_response() -> None:
    before_count = len(mcp_server._adapter._plans)

    first = _call_direct_sync(
        mars_plan(
            goal=_goal_payload(_goal()),
            evidence=_evidence_payload(_evidence()),
            request_id="req-idempotent-plan",
        )
    )
    second = _call_direct_sync(
        mars_plan(
            goal=_goal_payload(_goal()),
            evidence=_evidence_payload(_evidence()),
            request_id="req-idempotent-plan",
        )
    )

    assert first == second
    assert len(mcp_server._adapter._plans) == before_count + 1


def test_transport_direct_duplicate_request_id_conflict_returns_invalid_request() -> None:
    original_goal = _goal_payload(_goal())
    changed_goal = dict(original_goal)
    changed_goal["crew_size"] = cast(int, original_goal["crew_size"]) + 1

    _call_direct_sync(
        mars_plan(
            goal=original_goal,
            evidence=_evidence_payload(_evidence()),
            request_id="req-idempotent-conflict",
        )
    )

    with pytest.raises(ToolError) as exc_info:
        _call_direct_sync(
            mars_plan(
                goal=changed_goal,
                evidence=_evidence_payload(_evidence()),
                request_id="req-idempotent-conflict",
            )
        )

    error_payload = _parse_tool_error(exc_info.value)
    assert error_payload["code"] == "invalid_request"
    assert error_payload["request_id"] == "req-idempotent-conflict"
    assert "already used" in str(error_payload["message"])


def test_transport_direct_timeout_does_not_commit_state_and_allows_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_count = len(mcp_server._adapter._plans)

    async def _timeout_bounded_sync(*args: object, **kwargs: object) -> object:
        raise TimeoutError("Tool execution exceeded timeout of 0.010 seconds")

    monkeypatch.setattr(mcp_server, "_run_bounded_sync", _timeout_bounded_sync)

    with pytest.raises(ToolError) as exc_info:
        _call_direct_sync(
            mars_plan(
                goal=_goal_payload(_goal()),
                evidence=_evidence_payload(_evidence()),
                request_id="req-timeout-plan",
            )
        )

    error_payload = _parse_tool_error(exc_info.value)
    assert error_payload["code"] == "deadline_exceeded"
    assert error_payload["request_id"] == "req-timeout-plan"
    assert len(mcp_server._adapter._plans) == before_count

    monkeypatch.undo()

    retried = _call_direct_sync(
        mars_plan(
            goal=_goal_payload(_goal()),
            evidence=_evidence_payload(_evidence()),
            request_id="req-timeout-plan",
        )
    )

    assert retried["ok"] is True
    assert retried["request_id"] == "req-timeout-plan"
    assert len(mcp_server._adapter._plans) == before_count + 1


def test_transport_stdio_full_pipeline_parity_matches_adapter() -> None:
    """Full 4-tool sequential pipeline over a single stdio session equals adapter.invoke()."""
    goal = _goal()
    evidence = _evidence()
    goal_payload = _goal_payload(goal)
    evidence_payload = _evidence_payload(evidence)

    # Run the reference pipeline directly via the adapter
    adapter = MarsMCPAdapter()
    ref_plan = adapter.invoke("mars.plan", {"goal": goal_payload, "evidence": evidence_payload})
    ref_plan_id = ref_plan["plan_id"]
    assert isinstance(ref_plan_id, str)

    ref_sim = adapter.invoke(
        "mars.simulate",
        {"plan_id": ref_plan_id, "seed": 42, "max_repair_attempts": 2},
    )
    ref_sim_id = ref_sim["simulation_id"]
    assert isinstance(ref_sim_id, str)

    ref_gov = adapter.invoke(
        "mars.governance",
        {"plan_id": ref_plan_id, "simulation_id": ref_sim_id, "min_confidence": 0.75},
    )
    ref_bench = adapter.invoke(
        "mars.benchmark",
        {"plan_id": ref_plan_id, "simulation_id": ref_sim_id},
    )

    # Run the same pipeline over a single stdio server process
    plan_r, sim_r, gov_r, bench_r = _run_stdio_pipeline_sync(goal_payload, evidence_payload)

    # All four calls must succeed
    assert plan_r.isError is False
    assert sim_r.isError is False
    assert gov_r.isError is False
    assert bench_r.isError is False

    # request_id echo for each call
    plan_content = plan_r.structuredContent
    sim_content = sim_r.structuredContent
    gov_content = gov_r.structuredContent
    bench_content = bench_r.structuredContent
    assert isinstance(plan_content, dict)
    assert isinstance(sim_content, dict)
    assert isinstance(gov_content, dict)
    assert isinstance(bench_content, dict)

    assert plan_content["request_id"] == "req-parity-plan"
    assert sim_content["request_id"] == "req-parity-sim"
    assert gov_content["request_id"] == "req-parity-gov"
    assert bench_content["request_id"] == "req-parity-bench"

    # Payload equality: stdio output matches adapter output (ignoring ok/request_id/created_at)
    assert _normalize_payload(_unwrap_success(plan_content)) == _normalize_payload(ref_plan)
    assert _normalize_payload(_unwrap_success(sim_content)) == _normalize_payload(ref_sim)
    assert _normalize_payload(_unwrap_success(gov_content)) == _normalize_payload(ref_gov)
    assert _normalize_payload(_unwrap_success(bench_content)) == _normalize_payload(ref_bench)


def test_transport_stdio_progress_callback_emits_events() -> None:
    goal_payload = _goal_payload(_goal())
    evidence_payload = _evidence_payload(_evidence())

    (_, progress_events, _) = _run_stdio_pipeline_with_observability_sync(
        goal_payload,
        evidence_payload,
    )

    assert progress_events
    messages = [item[2] for item in progress_events if item[2] is not None]
    assert any("mars.plan started" in message for message in messages)
    assert any("mars.plan completed" in message for message in messages)


def test_transport_stdio_correlation_id_propagates_across_pipeline() -> None:
    goal_payload = _goal_payload(_goal())
    evidence_payload = _evidence_payload(_evidence())

    (_, _, log_messages) = _run_stdio_pipeline_with_observability_sync(
        goal_payload,
        evidence_payload,
        env_overrides={
            "MARS_MCP_AUTH_ENABLED": "false",
        },
    )

    started_messages = [msg for msg in log_messages if " started request_id=" in msg]
    assert len(started_messages) >= 4

    def _corr(msg: str) -> str:
        marker = "correlation_id="
        idx = msg.find(marker)
        assert idx >= 0
        return msg[idx + len(marker) :].strip()

    correlations = [_corr(msg) for msg in started_messages[:4]]
    assert len(set(correlations)) == 1


def test_observability_stderr_logs_have_stable_keys_and_no_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _call_direct_sync(
        mars_plan(
            goal=_goal_payload(_goal()),
            evidence=_evidence_payload(_evidence()),
            request_id="req-observe-direct",
            auth_token="secret-token",
        )
    )

    captured = capsys.readouterr()
    lines = [line for line in captured.err.splitlines() if line.strip().startswith("{")]
    assert lines
    parsed = [json.loads(line) for line in lines]
    assert all(isinstance(item, dict) for item in parsed)

    for item in parsed:
        assert "event" in item
        assert "tool" in item
        assert "request_id" in item
        assert "correlation_id" in item
        assert "outcome" in item
        assert "latency_ms" in item
        assert "auth_enabled" in item
        assert "error_code" in item

    assert "secret-token" not in captured.err


def test_observability_auth_failure_logs_error_code_and_outcome(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARS_MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("MARS_MCP_AUTH_TOKEN", "expected-token")

    with pytest.raises(ToolError):
        _call_direct_sync(
            mars_plan(
                goal=_goal_payload(_goal()),
                evidence=_evidence_payload(_evidence()),
                request_id="req-observe-auth-fail",
            )
        )

    captured = capsys.readouterr()
    lines = [line for line in captured.err.splitlines() if line.strip().startswith("{")]
    assert lines
    parsed = [json.loads(line) for line in lines]
    error_events = [item for item in parsed if item.get("event") == "tool.error"]
    assert error_events
    assert any(item.get("outcome") == "failure" for item in error_events)
    assert any(item.get("error_code") == "unauthenticated" for item in error_events)
