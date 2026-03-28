"""Phase 8 MCP server runtime wrapping MarsMCPAdapter via FastMCP."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.server import Context

from mars_agent.mcp.adapter import MarsMCPAdapter, MCPValue

_adapter = MarsMCPAdapter()
mcp = FastMCP("mars-colonization-specialist")
_TOOL_ERROR_SCHEMA_VERSION = "1.0"
_AUTH_ENABLED_ENV = "MARS_MCP_AUTH_ENABLED"
_AUTH_TOKEN_ENV = "MARS_MCP_AUTH_TOKEN"
_AUTH_PERMISSIONS_ENV = "MARS_MCP_AUTH_PERMISSIONS"
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
        payload = _adapter.invoke(tool_name, arguments)
        response = _as_object_dict(payload)
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
    except Exception as exc:  # noqa: BLE001
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
