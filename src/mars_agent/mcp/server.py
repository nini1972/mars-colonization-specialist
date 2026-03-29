"""Phase 8 MCP server runtime wrapping MarsMCPAdapter via FastMCP."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeVar, cast
from uuid import uuid4

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.server import Context

from mars_agent.governance import GovernanceGate
from mars_agent.mcp.adapter import (
    MarsMCPAdapter,
    MCPValue,
    _optional_float,
    _optional_int,
    _parse_evidence,
    _parse_goal,
    _require_mapping,
    _require_string,
    to_mcp_value,
)
from mars_agent.orchestration import MissionGoal
from mars_agent.orchestration.models import PlanResult
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.simulation import SimulationPipeline
from mars_agent.simulation.pipeline import SimulationReport

_adapter = MarsMCPAdapter()
mcp = FastMCP("mars-colonization-specialist")
_TOOL_ERROR_SCHEMA_VERSION = "1.0"
_AUTH_ENABLED_ENV = "MARS_MCP_AUTH_ENABLED"
_AUTH_TOKEN_ENV = "MARS_MCP_AUTH_TOKEN"
_AUTH_PERMISSIONS_ENV = "MARS_MCP_AUTH_PERMISSIONS"
_TOOL_TIMEOUT_SECONDS_ENV = "MARS_MCP_TOOL_TIMEOUT_SECONDS"
_IDEMPOTENCY_TTL_SECONDS_ENV = "MARS_MCP_IDEMPOTENCY_TTL_SECONDS"
_IDEMPOTENCY_MAX_ENTRIES_ENV = "MARS_MCP_IDEMPOTENCY_MAX_ENTRIES"
_DEFAULT_IDEMPOTENCY_TTL_SECONDS = 900.0
_DEFAULT_IDEMPOTENCY_MAX_ENTRIES = 512
_TOOL_METRIC_KEYS = ("calls", "successes", "failures", "auth_failures", "total_latency_ms")
_ALL_TOOL_PERMISSIONS = {"plan", "simulate", "governance", "benchmark"}
_TOOL_PERMISSION_BY_NAME = {
    "mars.plan": "plan",
    "mars.simulate": "simulate",
    "mars.governance": "governance",
    "mars.benchmark": "benchmark",
}
_TOOL_METRICS: dict[str, dict[str, float]] = {}
_PLAN_CORRELATION_BY_ID: dict[str, str] = {}
_SIMULATION_CORRELATION_BY_ID: dict[str, str] = {}


@dataclass(slots=True)
class _IdempotencyEntry:
    tool_name: str
    fingerprint: str
    response: dict[str, object]
    completed_at_monotonic: float


_COMPLETED_REQUESTS: dict[str, _IdempotencyEntry] = {}
_IN_FLIGHT_REQUESTS: dict[str, tuple[str, str]] = {}
_SyncResult = TypeVar("_SyncResult")


def _as_object_dict(payload: dict[str, MCPValue]) -> dict[str, object]:
    return {key: cast(object, value) for key, value in payload.items()}


def _resolve_request_id(request_id: str | None) -> str:
    if request_id is not None and request_id.strip():
        return request_id.strip()
    return f"req-{uuid4().hex}"


def _tool_error_message(request_id: str, tool_name: str, code: str, message: str) -> str:
    return json.dumps(
        {
            "schema_version": _TOOL_ERROR_SCHEMA_VERSION,
            "tool": tool_name,
            "code": code,
            "request_id": request_id,
            "message": message,
        }
    )


def _parse_bool_env(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _canonicalize_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [_canonicalize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize_json_value(item) for item in value]
    return value


def _request_fingerprint(tool_name: str, arguments: Mapping[str, object]) -> str:
    return json.dumps(
        {
            "tool": tool_name,
            "arguments": _canonicalize_json_value(dict(arguments)),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _auth_enabled() -> bool:
    return _parse_bool_env(os.getenv(_AUTH_ENABLED_ENV), default=False)


def _configured_auth_token() -> str | None:
    value = os.getenv(_AUTH_TOKEN_ENV)
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _configured_permissions() -> set[str]:
    value = os.getenv(_AUTH_PERMISSIONS_ENV)
    if value is None:
        return set(_ALL_TOOL_PERMISSIONS)
    allowed = {item.strip() for item in value.split(",") if item.strip()}
    return allowed if allowed else set()


def _configured_tool_timeout_seconds() -> float | None:
    raw = os.getenv(_TOOL_TIMEOUT_SECONDS_ENV)
    if raw is None:
        return None

    stripped = raw.strip()
    if not stripped:
        return None

    try:
        timeout_seconds = float(stripped)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid {_TOOL_TIMEOUT_SECONDS_ENV} value: must be numeric"
        ) from exc

    if timeout_seconds <= 0:
        raise RuntimeError(
            f"Invalid {_TOOL_TIMEOUT_SECONDS_ENV} value: must be greater than zero"
        )

    return timeout_seconds


def _configured_idempotency_ttl_seconds() -> float:
    raw = os.getenv(_IDEMPOTENCY_TTL_SECONDS_ENV)
    if raw is None:
        return _DEFAULT_IDEMPOTENCY_TTL_SECONDS

    stripped = raw.strip()
    if not stripped:
        return _DEFAULT_IDEMPOTENCY_TTL_SECONDS

    try:
        ttl_seconds = float(stripped)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid {_IDEMPOTENCY_TTL_SECONDS_ENV} value: must be numeric"
        ) from exc

    if ttl_seconds <= 0:
        raise RuntimeError(
            f"Invalid {_IDEMPOTENCY_TTL_SECONDS_ENV} value: must be greater than zero"
        )

    return ttl_seconds


def _configured_idempotency_max_entries() -> int:
    raw = os.getenv(_IDEMPOTENCY_MAX_ENTRIES_ENV)
    if raw is None:
        return _DEFAULT_IDEMPOTENCY_MAX_ENTRIES

    stripped = raw.strip()
    if not stripped:
        return _DEFAULT_IDEMPOTENCY_MAX_ENTRIES

    try:
        max_entries = int(stripped)
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid {_IDEMPOTENCY_MAX_ENTRIES_ENV} value: must be an integer"
        ) from exc

    if max_entries <= 0:
        raise RuntimeError(
            f"Invalid {_IDEMPOTENCY_MAX_ENTRIES_ENV} value: must be greater than zero"
        )

    return max_entries


def _idempotency_now_seconds() -> float:
    return perf_counter()


def _prune_completed_idempotency_entries(now: float | None = None) -> None:
    resolved_now = _idempotency_now_seconds() if now is None else now
    ttl_seconds = _configured_idempotency_ttl_seconds()
    max_entries = _configured_idempotency_max_entries()

    expired_request_ids = [
        req_id
        for req_id, entry in _COMPLETED_REQUESTS.items()
        if (resolved_now - entry.completed_at_monotonic) > ttl_seconds
    ]
    for req_id in expired_request_ids:
        _COMPLETED_REQUESTS.pop(req_id, None)

    while len(_COMPLETED_REQUESTS) > max_entries:
        oldest_request_id = min(
            _COMPLETED_REQUESTS,
            key=lambda req_id: _COMPLETED_REQUESTS[req_id].completed_at_monotonic,
        )
        _COMPLETED_REQUESTS.pop(oldest_request_id, None)


def _request_meta_dict() -> dict[str, object]:
    try:
        context = mcp.get_context()
        meta = context.request_context.meta
    except Exception:  # noqa: BLE001
        return {}

    if meta is None:
        return {}

    return cast(dict[str, object], meta.model_dump(exclude_none=True))


def _extract_correlation_id_from_meta() -> str | None:
    meta = _request_meta_dict()
    raw = meta.get("correlation_id") or meta.get("trace_id")
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value if value else None


def _extract_mission_id(tool_name: str, arguments: Mapping[str, object]) -> str | None:
    if tool_name != "mars.plan":
        return None
    goal = arguments.get("goal")
    if not isinstance(goal, Mapping):
        return None
    raw_mission_id = goal.get("mission_id")
    if not isinstance(raw_mission_id, str):
        return None
    mission_id = raw_mission_id.strip()
    return mission_id if mission_id else None


def _resolve_correlation_id(
    tool_name: str,
    arguments: Mapping[str, object],
    request_id: str,
) -> str:
    meta_correlation = _extract_correlation_id_from_meta()
    if tool_name == "mars.plan":
        return meta_correlation or request_id

    simulation_id = arguments.get("simulation_id")
    if isinstance(simulation_id, str) and simulation_id in _SIMULATION_CORRELATION_BY_ID:
        return _SIMULATION_CORRELATION_BY_ID[simulation_id]

    plan_id = arguments.get("plan_id")
    if isinstance(plan_id, str) and plan_id in _PLAN_CORRELATION_BY_ID:
        return _PLAN_CORRELATION_BY_ID[plan_id]

    return meta_correlation or request_id


def _emit_stderr_log(record: Mapping[str, object]) -> None:
    print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)


def _record_metric(
    tool_name: str,
    *,
    success: bool,
    latency_ms: float,
    auth_failure: bool = False,
) -> None:
    bucket = _TOOL_METRICS.setdefault(tool_name, {key: 0.0 for key in _TOOL_METRIC_KEYS})
    bucket["calls"] += 1.0
    bucket["total_latency_ms"] += latency_ms
    if success:
        bucket["successes"] += 1.0
        return
    bucket["failures"] += 1.0
    if auth_failure:
        bucket["auth_failures"] += 1.0


def _emit_observability_log(
    *,
    event: str,
    tool_name: str,
    request_id: str,
    correlation_id: str,
    mission_id: str | None,
    outcome: str,
    latency_ms: float,
    auth_enabled: bool,
    error_code: str | None,
) -> None:
    _emit_stderr_log(
        {
            "event": event,
            "tool": tool_name,
            "request_id": request_id,
            "correlation_id": correlation_id,
            "mission_id": mission_id,
            "outcome": outcome,
            "latency_ms": round(latency_ms, 3),
            "auth_enabled": auth_enabled,
            "error_code": error_code,
        }
    )


def _error_code_from_tool_error(exc: ToolError) -> str | None:
    payload = str(exc)
    start = payload.find("{")
    if start >= 0:
        payload = payload[start:]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    code = data.get("code")
    return code if isinstance(code, str) else None


def _try_context() -> Context[Any, Any, Any] | None:
    try:
        return mcp.get_context()
    except Exception:  # noqa: BLE001
        return None


async def _report_progress_safe(progress: float, total: float, message: str) -> None:
    context = _try_context()
    if context is None:
        return
    try:
        await context.report_progress(progress=progress, total=total, message=message)
    except Exception:  # noqa: BLE001
        return


async def _log_to_client_safe(level: str, message: str) -> None:
    context = _try_context()
    if context is None:
        return
    try:
        if level == "error":
            await context.error(message)
        elif level == "warning":
            await context.warning(message)
        elif level == "debug":
            await context.debug(message)
        else:
            await context.info(message)
    except Exception:  # noqa: BLE001
        return


def _extract_token_from_meta() -> str | None:
    meta = _request_meta_dict()
    raw = meta.get("authorization") or meta.get("auth_token") or meta.get("token")

    if not isinstance(raw, str):
        return None

    value = raw.strip()
    if not value:
        return None

    if value.lower().startswith("bearer "):
        bearer = value[7:].strip()
        return bearer if bearer else None

    return value


def _resolve_auth_token(auth_token: str | None) -> str | None:
    if auth_token is not None and auth_token.strip():
        return auth_token.strip()
    return _extract_token_from_meta()


def _raise_tool_error(request_id: str, tool_name: str, code: str, message: str) -> None:
    raise ToolError(
        _tool_error_message(
            request_id=request_id,
            tool_name=tool_name,
            code=code,
            message=message,
        )
    )


def _validate_goal_bounds(goal: MissionGoal) -> None:
    if goal.crew_size <= 0:
        raise ValueError("goal.crew_size must be greater than zero")
    if goal.horizon_years <= 0:
        raise ValueError("goal.horizon_years must be greater than zero")
    if goal.solar_generation_kw < 0:
        raise ValueError("goal.solar_generation_kw must be non-negative")
    if goal.battery_capacity_kwh < 0:
        raise ValueError("goal.battery_capacity_kwh must be non-negative")
    if not 0.0 <= goal.dust_degradation_fraction <= 1.0:
        raise ValueError("goal.dust_degradation_fraction must be between 0.0 and 1.0")
    if goal.hours_without_sun < 0:
        raise ValueError("goal.hours_without_sun must be non-negative")
    if not 0.0 <= goal.desired_confidence <= 1.0:
        raise ValueError("goal.desired_confidence must be between 0.0 and 1.0")


def _validate_evidence_bounds(evidence: tuple[EvidenceReference, ...]) -> None:
    if not evidence:
        raise ValueError("Argument 'evidence' must contain at least one item")
    for index, item in enumerate(evidence):
        if not 0.0 <= item.relevance_score <= 1.0:
            raise ValueError(
                f"evidence[{index}].relevance_score must be between 0.0 and 1.0"
            )


def _validate_transport_arguments(tool_name: str, arguments: Mapping[str, object]) -> None:
    if tool_name == "mars.plan":
        goal = _parse_goal(_require_mapping(arguments, "goal"))
        evidence = _parse_evidence(arguments)
        _validate_goal_bounds(goal)
        _validate_evidence_bounds(evidence)
        return

    if tool_name == "mars.simulate":
        _require_string(arguments, "plan_id")
        max_repair_attempts = _optional_int(arguments, "max_repair_attempts", 3)
        if max_repair_attempts < 0:
            raise ValueError("Argument 'max_repair_attempts' must be non-negative")
        _optional_int(arguments, "seed", 42)
        return

    if tool_name == "mars.governance":
        _require_string(arguments, "plan_id")
        _require_string(arguments, "simulation_id")
        min_confidence = _optional_float(arguments, "min_confidence", 0.85)
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("Argument 'min_confidence' must be between 0.0 and 1.0")
        return

    if tool_name == "mars.benchmark":
        _require_string(arguments, "plan_id")
        _require_string(arguments, "simulation_id")
        return

    raise KeyError(f"Unsupported MCP tool: {tool_name}")


def _prepare_idempotent_request(
    request_id: str,
    tool_name: str,
    fingerprint: str,
) -> dict[str, object] | None:
    _prune_completed_idempotency_entries()
    completed = _COMPLETED_REQUESTS.get(request_id)
    if completed is not None:
        if completed.tool_name != tool_name or completed.fingerprint != fingerprint:
            raise ValueError(
                f"request_id '{request_id}' was already used for a different invocation"
            )
        return deepcopy(completed.response)

    in_flight = _IN_FLIGHT_REQUESTS.get(request_id)
    if in_flight is not None:
        in_flight_tool, in_flight_fingerprint = in_flight
        if in_flight_tool != tool_name or in_flight_fingerprint != fingerprint:
            raise ValueError(
                f"request_id '{request_id}' is already in progress for a different invocation"
            )
        raise ValueError(f"request_id '{request_id}' is already in progress")

    _IN_FLIGHT_REQUESTS[request_id] = (tool_name, fingerprint)
    return None


def _complete_idempotent_request(
    request_id: str,
    tool_name: str,
    fingerprint: str,
    response: dict[str, object],
) -> None:
    _IN_FLIGHT_REQUESTS.pop(request_id, None)
    _COMPLETED_REQUESTS[request_id] = _IdempotencyEntry(
        tool_name=tool_name,
        fingerprint=fingerprint,
        response=deepcopy(response),
        completed_at_monotonic=_idempotency_now_seconds(),
    )
    _prune_completed_idempotency_entries()


def _reset_in_flight_request(request_id: str) -> None:
    _IN_FLIGHT_REQUESTS.pop(request_id, None)


async def _run_bounded_sync(  # noqa: UP047
    func: Callable[[], _SyncResult],
    *,
    timeout_seconds: float | None,
) -> _SyncResult:
    if timeout_seconds is None:
        return await anyio.to_thread.run_sync(func)

    try:
        with anyio.fail_after(timeout_seconds):
            return await anyio.to_thread.run_sync(func, abandon_on_cancel=True)
    except TimeoutError as exc:
        raise TimeoutError(
            f"Tool execution exceeded timeout of {timeout_seconds:.3f} seconds"
        ) from exc


async def _invoke_runtime_tool(
    tool_name: str,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    if tool_name == "mars.plan":
        goal = _parse_goal(_require_mapping(arguments, "goal"))
        evidence = _parse_evidence(arguments)

        def _compute_plan() -> PlanResult:
            return _adapter.planner.plan(goal=goal, evidence=evidence)

        plan: PlanResult = await _run_bounded_sync(
            _compute_plan,
            timeout_seconds=_configured_tool_timeout_seconds(),
        )
        plan_id = f"plan-{len(_adapter._plans) + 1:04d}"
        _adapter._plans[plan_id] = plan
        return {
            "plan_id": plan_id,
            "plan": cast(object, to_mcp_value(plan)),
        }

    if tool_name == "mars.simulate":
        plan_id = _require_string(arguments, "plan_id")
        seed = _optional_int(arguments, "seed", _adapter.simulation_pipeline.seed)
        max_repair_attempts = _optional_int(
            arguments,
            "max_repair_attempts",
            _adapter.simulation_pipeline.max_repair_attempts,
        )
        plan_result = _adapter._plans.get(plan_id)
        if plan_result is None:
            raise KeyError(f"Unknown plan_id: {plan_id}")
        plan = plan_result

        def _compute_simulation() -> SimulationReport:
            pipeline = SimulationPipeline(seed=seed, max_repair_attempts=max_repair_attempts)
            return pipeline.run(plan)

        simulation: SimulationReport = await _run_bounded_sync(
            _compute_simulation,
            timeout_seconds=_configured_tool_timeout_seconds(),
        )
        simulation_id = f"simulation-{len(_adapter._simulations) + 1:04d}"
        _adapter._simulations[simulation_id] = simulation
        return {
            "simulation_id": simulation_id,
            "simulation": cast(object, to_mcp_value(simulation)),
        }

    if tool_name == "mars.governance":
        plan_id = _require_string(arguments, "plan_id")
        simulation_id = _require_string(arguments, "simulation_id")
        min_confidence = _optional_float(
            arguments,
            "min_confidence",
            _adapter.governance_gate.min_confidence,
        )
        plan_result = _adapter._plans.get(plan_id)
        if plan_result is None:
            raise KeyError(f"Unknown plan_id: {plan_id}")
        simulation_result = _adapter._simulations.get(simulation_id)
        if simulation_result is None:
            raise KeyError(f"Unknown simulation_id: {simulation_id}")
        plan = plan_result
        simulation = simulation_result

        def _compute_governance() -> object:
            return GovernanceGate(min_confidence=min_confidence).evaluate(
                plan=plan,
                simulation=simulation,
            )

        governance: object = await _run_bounded_sync(
            _compute_governance,
            timeout_seconds=_configured_tool_timeout_seconds(),
        )
        return {"governance": cast(object, to_mcp_value(governance))}

    if tool_name == "mars.benchmark":
        plan_id = _require_string(arguments, "plan_id")
        simulation_id = _require_string(arguments, "simulation_id")
        plan_result = _adapter._plans.get(plan_id)
        if plan_result is None:
            raise KeyError(f"Unknown plan_id: {plan_id}")
        simulation_result = _adapter._simulations.get(simulation_id)
        if simulation_result is None:
            raise KeyError(f"Unknown simulation_id: {simulation_id}")
        plan = plan_result
        simulation = simulation_result

        def _compute_benchmark() -> object:
            return _adapter.benchmark_harness.run(plan=plan, simulation=simulation)

        benchmark: object = await _run_bounded_sync(
            _compute_benchmark,
            timeout_seconds=_configured_tool_timeout_seconds(),
        )
        return {"benchmark": cast(object, to_mcp_value(benchmark))}

    raise KeyError(f"Unsupported MCP tool: {tool_name}")


def _enforce_auth(tool_name: str, request_id: str, auth_token: str | None) -> None:
    if not _auth_enabled():
        return

    expected_token = _configured_auth_token()
    if expected_token is None:
        _raise_tool_error(
            request_id=request_id,
            tool_name=tool_name,
            code="unauthenticated",
            message=(
                f"Server auth misconfigured: {_AUTH_TOKEN_ENV} is required "
                "when auth is enabled."
            ),
        )

    presented_token = _resolve_auth_token(auth_token)
    if presented_token is None or presented_token != expected_token:
        _raise_tool_error(
            request_id=request_id,
            tool_name=tool_name,
            code="unauthenticated",
            message="Missing or invalid bearer token.",
        )

    required_permission = _TOOL_PERMISSION_BY_NAME.get(tool_name)
    allowed_permissions = _configured_permissions()
    if required_permission is not None and required_permission not in allowed_permissions:
        _raise_tool_error(
            request_id=request_id,
            tool_name=tool_name,
            code="forbidden",
            message=(
                f"Token is authenticated but lacks required permission '{required_permission}' "
                f"for tool '{tool_name}'."
            ),
        )


async def _invoke_with_envelope(
    tool_name: str,
    arguments: Mapping[str, object],
    request_id: str | None,
    auth_token: str | None,
) -> dict[str, object]:
    resolved_request_id = _resolve_request_id(request_id)
    request_fingerprint = _request_fingerprint(tool_name=tool_name, arguments=arguments)
    correlation_id = _resolve_correlation_id(
        tool_name=tool_name,
        arguments=arguments,
        request_id=resolved_request_id,
    )
    mission_id = _extract_mission_id(tool_name=tool_name, arguments=arguments)
    auth_enabled = _auth_enabled()

    _emit_observability_log(
        event="tool.start",
        tool_name=tool_name,
        request_id=resolved_request_id,
        correlation_id=correlation_id,
        mission_id=mission_id,
        outcome="started",
        latency_ms=0.0,
        auth_enabled=auth_enabled,
        error_code=None,
    )
    await _report_progress_safe(10, 100, f"{tool_name} started")
    await _log_to_client_safe(
        "info",
        f"{tool_name} started request_id={resolved_request_id} correlation_id={correlation_id}",
    )

    started = perf_counter()

    try:
        _enforce_auth(tool_name=tool_name, request_id=resolved_request_id, auth_token=auth_token)
        _validate_transport_arguments(tool_name=tool_name, arguments=arguments)

        replayed_response = _prepare_idempotent_request(
            request_id=resolved_request_id,
            tool_name=tool_name,
            fingerprint=request_fingerprint,
        )
        if replayed_response is not None:
            latency_ms = (perf_counter() - started) * 1000.0
            _record_metric(tool_name, success=True, latency_ms=latency_ms)
            _emit_observability_log(
                event="tool.success",
                tool_name=tool_name,
                request_id=resolved_request_id,
                correlation_id=correlation_id,
                mission_id=mission_id,
                outcome="success",
                latency_ms=latency_ms,
                auth_enabled=auth_enabled,
                error_code=None,
            )
            await _report_progress_safe(100, 100, f"{tool_name} completed")
            await _log_to_client_safe(
                "info",
                (
                    f"{tool_name} completed request_id={resolved_request_id} "
                    f"correlation_id={correlation_id} replayed=true"
                ),
            )
            return replayed_response

        response = await _invoke_runtime_tool(tool_name=tool_name, arguments=arguments)
        plan_id = response.get("plan_id")
        if isinstance(plan_id, str):
            _PLAN_CORRELATION_BY_ID[plan_id] = correlation_id
        simulation_id = response.get("simulation_id")
        if isinstance(simulation_id, str):
            _SIMULATION_CORRELATION_BY_ID[simulation_id] = correlation_id

        if tool_name == "mars.simulate":
            await _report_progress_safe(75, 100, "simulation completed")

        response["request_id"] = resolved_request_id
        response["ok"] = True
        _complete_idempotent_request(
            request_id=resolved_request_id,
            tool_name=tool_name,
            fingerprint=request_fingerprint,
            response=response,
        )

        latency_ms = (perf_counter() - started) * 1000.0
        _record_metric(tool_name, success=True, latency_ms=latency_ms)
        _emit_observability_log(
            event="tool.success",
            tool_name=tool_name,
            request_id=resolved_request_id,
            correlation_id=correlation_id,
            mission_id=mission_id,
            outcome="success",
            latency_ms=latency_ms,
            auth_enabled=auth_enabled,
            error_code=None,
        )
        await _report_progress_safe(100, 100, f"{tool_name} completed")
        await _log_to_client_safe(
            "info",
            (
                f"{tool_name} completed request_id={resolved_request_id} "
                f"correlation_id={correlation_id}"
            ),
        )
        return response
    except ToolError as exc:
        latency_ms = (perf_counter() - started) * 1000.0
        error_code = _error_code_from_tool_error(exc)
        _record_metric(
            tool_name,
            success=False,
            latency_ms=latency_ms,
            auth_failure=error_code in {"unauthenticated", "forbidden"},
        )
        _emit_observability_log(
            event="tool.error",
            tool_name=tool_name,
            request_id=resolved_request_id,
            correlation_id=correlation_id,
            mission_id=mission_id,
            outcome="failure",
            latency_ms=latency_ms,
            auth_enabled=auth_enabled,
            error_code=error_code,
        )
        await _log_to_client_safe(
            "error",
            (
                f"{tool_name} failed request_id={resolved_request_id} "
                f"correlation_id={correlation_id} code={error_code or 'unknown'}"
            ),
        )
        raise
    except (KeyError, TypeError, ValueError) as exc:
        _reset_in_flight_request(resolved_request_id)
        latency_ms = (perf_counter() - started) * 1000.0
        _record_metric(tool_name, success=False, latency_ms=latency_ms)
        _emit_observability_log(
            event="tool.error",
            tool_name=tool_name,
            request_id=resolved_request_id,
            correlation_id=correlation_id,
            mission_id=mission_id,
            outcome="failure",
            latency_ms=latency_ms,
            auth_enabled=auth_enabled,
            error_code="invalid_request",
        )
        await _log_to_client_safe(
            "error",
            (
                f"{tool_name} failed request_id={resolved_request_id} "
                f"correlation_id={correlation_id} code=invalid_request"
            ),
        )
        raise ToolError(
            _tool_error_message(
                request_id=resolved_request_id,
                tool_name=tool_name,
                code="invalid_request",
                message=str(exc),
            )
        ) from exc
    except TimeoutError as exc:
        _reset_in_flight_request(resolved_request_id)
        latency_ms = (perf_counter() - started) * 1000.0
        _record_metric(tool_name, success=False, latency_ms=latency_ms)
        _emit_observability_log(
            event="tool.error",
            tool_name=tool_name,
            request_id=resolved_request_id,
            correlation_id=correlation_id,
            mission_id=mission_id,
            outcome="failure",
            latency_ms=latency_ms,
            auth_enabled=auth_enabled,
            error_code="deadline_exceeded",
        )
        await _log_to_client_safe(
            "error",
            (
                f"{tool_name} failed request_id={resolved_request_id} "
                f"correlation_id={correlation_id} code=deadline_exceeded"
            ),
        )
        raise ToolError(
            _tool_error_message(
                request_id=resolved_request_id,
                tool_name=tool_name,
                code="deadline_exceeded",
                message=str(exc),
            )
        ) from exc
    except Exception as exc:  # noqa: BLE001
        _reset_in_flight_request(resolved_request_id)
        latency_ms = (perf_counter() - started) * 1000.0
        _record_metric(tool_name, success=False, latency_ms=latency_ms)
        _emit_observability_log(
            event="tool.error",
            tool_name=tool_name,
            request_id=resolved_request_id,
            correlation_id=correlation_id,
            mission_id=mission_id,
            outcome="failure",
            latency_ms=latency_ms,
            auth_enabled=auth_enabled,
            error_code="internal_error",
        )
        await _log_to_client_safe(
            "error",
            (
                f"{tool_name} failed request_id={resolved_request_id} "
                f"correlation_id={correlation_id} code=internal_error"
            ),
        )
        raise ToolError(
            _tool_error_message(
                request_id=resolved_request_id,
                tool_name=tool_name,
                code="internal_error",
                message=str(exc),
            )
        ) from exc


async def mars_plan(
    goal: dict[str, object],
    evidence: list[dict[str, object]],
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Generate a mission plan from goal and evidence inputs."""

    return await _invoke_with_envelope(
        tool_name="mars.plan",
        arguments={"goal": goal, "evidence": evidence},
        request_id=request_id,
        auth_token=auth_token,
    )


async def mars_simulate(
    plan_id: str,
    seed: int = 42,
    max_repair_attempts: int = 3,
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Run deterministic simulation pipeline for a previously generated plan."""

    return await _invoke_with_envelope(
        tool_name="mars.simulate",
        arguments={
            "plan_id": plan_id,
            "seed": seed,
            "max_repair_attempts": max_repair_attempts,
        },
        request_id=request_id,
        auth_token=auth_token,
    )


async def mars_governance(
    plan_id: str,
    simulation_id: str,
    min_confidence: float = 0.85,
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Evaluate governance gate using plan and simulation outputs."""

    return await _invoke_with_envelope(
        tool_name="mars.governance",
        arguments={
            "plan_id": plan_id,
            "simulation_id": simulation_id,
            "min_confidence": min_confidence,
        },
        request_id=request_id,
        auth_token=auth_token,
    )


async def mars_benchmark(
    plan_id: str,
    simulation_id: str,
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Evaluate benchmark deltas using plan and simulation outputs."""

    return await _invoke_with_envelope(
        tool_name="mars.benchmark",
        arguments={
            "plan_id": plan_id,
            "simulation_id": simulation_id,
        },
        request_id=request_id,
        auth_token=auth_token,
    )


mcp.tool(name="mars.plan")(mars_plan)
mcp.tool(name="mars.simulate")(mars_simulate)
mcp.tool(name="mars.governance")(mars_governance)
mcp.tool(name="mars.benchmark")(mars_benchmark)
