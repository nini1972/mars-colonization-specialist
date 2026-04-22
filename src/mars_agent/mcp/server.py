"""Phase 8 MCP server runtime wrapping MarsMCPAdapter via FastMCP."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol, TypeVar, cast
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
    _optional_string,
    _parse_evidence,
    _parse_goal,
    _require_mapping,
    _require_string,
    _require_string_sequence,
    to_mcp_value,
)
from mars_agent.mcp.persistence import (
    IdempotencyEntry,
    PersistenceCorruptionError,
    PersistenceSnapshot,
    PersistenceUnavailableError,
    SQLitePersistenceBackend,
)
from mars_agent.mcp.telemetry import TelemetryQueryService
from mars_agent.orchestration import MissionGoal
from mars_agent.orchestration.models import PlanResult, SpecialistTiming
from mars_agent.orchestration.registry import SpecialistRegistry
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.simulation import SimulationPipeline
from mars_agent.simulation.pipeline import SimulationReport
from mars_agent.specialists import (
    ECLSSSpecialist,
    HabitatThermodynamicsSpecialist,
    ISRUSpecialist,
    PowerSpecialist,
)
from mars_agent.specialists.contracts import (
    ModuleRequest,
    ModuleResponse,
    SpecialistCapability,
    Subsystem,
    TradeoffProposal,
    TradeoffReview,
)

_adapter = MarsMCPAdapter()
mcp = FastMCP("mars-colonization-specialist", streamable_http_path="/")
_TOOL_ERROR_SCHEMA_VERSION = "1.0"
_AUTH_ENABLED_ENV = "MARS_MCP_AUTH_ENABLED"
_AUTH_TOKEN_ENV = "MARS_MCP_AUTH_TOKEN"
_AUTH_PERMISSIONS_ENV = "MARS_MCP_AUTH_PERMISSIONS"
_TOOL_TIMEOUT_SECONDS_ENV = "MARS_MCP_TOOL_TIMEOUT_SECONDS"
_PLANNER_ASYNC_RUNTIME_ENV = "MARS_MCP_PLANNER_ASYNC"
_IDEMPOTENCY_TTL_SECONDS_ENV = "MARS_MCP_IDEMPOTENCY_TTL_SECONDS"
_IDEMPOTENCY_MAX_ENTRIES_ENV = "MARS_MCP_IDEMPOTENCY_MAX_ENTRIES"
_PERSISTENCE_BACKEND_ENV = "MARS_MCP_PERSISTENCE_BACKEND"
_PERSISTENCE_SQLITE_PATH_ENV = "MARS_MCP_PERSISTENCE_SQLITE_PATH"
_PERSISTENCE_CORRUPTION_FALLBACK_ENV = "MARS_MCP_PERSISTENCE_FALLBACK_ON_CORRUPTION"
_DEV_FAIL_SPECIALIST_ENV = "MARS_DEV_FAIL_SPECIALIST"
_DEFAULT_IDEMPOTENCY_TTL_SECONDS = 900.0
_DEFAULT_IDEMPOTENCY_MAX_ENTRIES = 512
_DEFAULT_PERSISTENCE_BACKEND = "memory"
_DEFAULT_PERSISTENCE_SQLITE_PATH = ".mars_mcp_runtime.sqlite3"
_TOOL_METRIC_KEYS = ("calls", "successes", "failures", "auth_failures", "total_latency_ms")
_PLAN_RUNTIME_METRIC_KEY = "mars.plan.runtime"
_PLAN_RUNTIME_METRIC_KEYS = ("sync_calls", "async_calls")
_SPECIALIST_METRIC_KEYS = (
    "calls",
    "total_latency_ms",
    "gate_pass",
    "gate_fail",
    "last_gate_accepted",
    "last_latency_ms",
    "fault_count",
    "last_failed",
)
_ALL_TOOL_PERMISSIONS = {"plan", "simulate", "governance", "benchmark", "release", "telemetry"}
_TOOL_PERMISSION_BY_NAME = {
    "mars.plan": "plan",
    "mars.simulate": "simulate",
    "mars.governance": "governance",
    "mars.benchmark": "benchmark",
    "mars.release": "release",
    "mars.telemetry.overview": "telemetry",
    "mars.telemetry.events": "telemetry",
    "mars.telemetry.invocation": "telemetry",
    "mars.telemetry.correlation": "telemetry",
    "mars.telemetry.dashboard": "telemetry",
}
_TOOL_METRICS: dict[str, dict[str, float]] = {}
_PLAN_CORRELATION_BY_ID: dict[str, str] = {}
_SIMULATION_CORRELATION_BY_ID: dict[str, str] = {}
_TELEMETRY = TelemetryQueryService()


_COMPLETED_REQUESTS: dict[str, IdempotencyEntry] = {}
_IN_FLIGHT_REQUESTS: dict[str, tuple[str, str]] = {}
_PERSISTENCE_BACKEND_NAME = _DEFAULT_PERSISTENCE_BACKEND
_PERSISTENCE_BACKEND: SQLitePersistenceBackend | None = None
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


def _configured_dev_fail_specialist() -> Subsystem | None:
    raw = os.getenv(_DEV_FAIL_SPECIALIST_ENV)
    if raw is None:
        return None

    value = raw.strip().lower()
    if not value:
        return None

    by_name = {subsystem.value: subsystem for subsystem in Subsystem}
    try:
        return by_name[value]
    except KeyError as exc:
        valid = ", ".join(sorted(by_name))
        raise RuntimeError(
            f"Invalid {_DEV_FAIL_SPECIALIST_ENV} value '{value}'. "
            f"Expected one of: {valid}"
        ) from exc


class _SupportsCapabilitiesAndAnalyze(Protocol):
    def capabilities(self) -> SpecialistCapability: ...

    def analyze(self, request: ModuleRequest) -> ModuleResponse: ...

    def propose_tradeoffs(self, conflict_ids: tuple[str, ...]) -> tuple[TradeoffProposal, ...]: ...

    def review_peer_proposals(
        self,
        proposals: tuple[TradeoffProposal, ...],
        conflict_ids: tuple[str, ...],
    ) -> tuple[TradeoffReview, ...]: ...


class _FailingSpecialist:
    """Dev-only specialist wrapper that raises on analyze()."""

    def __init__(
        self,
        subsystem: Subsystem,
        delegate: _SupportsCapabilitiesAndAnalyze,
    ) -> None:
        self._subsystem = subsystem
        self._delegate = delegate

    def capabilities(self) -> SpecialistCapability:
        return self._delegate.capabilities()

    def analyze(self, request: ModuleRequest) -> ModuleResponse:
        raise RuntimeError(
            f"Simulated failure in {self._subsystem.value} "
            f"via {_DEV_FAIL_SPECIALIST_ENV}"
        )

    def propose_tradeoffs(self, conflict_ids: tuple[str, ...]) -> tuple[TradeoffProposal, ...]:
        return self._delegate.propose_tradeoffs(conflict_ids)

    def review_peer_proposals(
        self,
        proposals: tuple[TradeoffProposal, ...],
        conflict_ids: tuple[str, ...],
    ) -> tuple[TradeoffReview, ...]:
        return self._delegate.review_peer_proposals(proposals, conflict_ids)


def _apply_dev_failure_injection() -> None:
    failing = _configured_dev_fail_specialist()
    if failing is None:
        return

    registry = SpecialistRegistry()
    specialists: dict[Subsystem, _SupportsCapabilitiesAndAnalyze] = {
        Subsystem.ECLSS: ECLSSSpecialist(),
        Subsystem.ISRU: ISRUSpecialist(),
        Subsystem.POWER: PowerSpecialist(),
        Subsystem.HABITAT_THERMODYNAMICS: HabitatThermodynamicsSpecialist(),
    }
    for subsystem, specialist in specialists.items():
        if subsystem == failing:
            registry.register(_FailingSpecialist(subsystem, specialist))
        else:
            registry.register(specialist)

    _adapter.planner.registry = registry
    print(
        (
            "[mars-mcp][dev] specialist failure injection enabled: "
            f"{_DEV_FAIL_SPECIALIST_ENV}={failing.value}"
        ),
        file=sys.stderr,
    )


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


def _planner_async_runtime_enabled() -> bool:
    return _parse_bool_env(os.getenv(_PLANNER_ASYNC_RUNTIME_ENV), default=False)


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


def _configured_persistence_backend() -> str:
    raw = os.getenv(_PERSISTENCE_BACKEND_ENV)
    if raw is None:
        return _DEFAULT_PERSISTENCE_BACKEND

    backend = raw.strip().lower()
    if not backend:
        return _DEFAULT_PERSISTENCE_BACKEND
    if backend not in {"memory", "sqlite"}:
        raise RuntimeError(
            f"Invalid {_PERSISTENCE_BACKEND_ENV} value: expected 'memory' or 'sqlite'"
        )
    return backend


def _configured_sqlite_path() -> Path:
    raw = os.getenv(_PERSISTENCE_SQLITE_PATH_ENV)
    if raw is None or not raw.strip():
        return Path.cwd() / _DEFAULT_PERSISTENCE_SQLITE_PATH
    return Path(raw.strip()).expanduser()


def _allow_corruption_fallback() -> bool:
    return _parse_bool_env(os.getenv(_PERSISTENCE_CORRUPTION_FALLBACK_ENV), default=False)


def _runtime_snapshot() -> PersistenceSnapshot:
    return PersistenceSnapshot(
        completed_requests={
            request_id: IdempotencyEntry(
                tool_name=entry.tool_name,
                fingerprint=entry.fingerprint,
                response=deepcopy(entry.response),
                completed_at_monotonic=entry.completed_at_monotonic,
            )
            for request_id, entry in _COMPLETED_REQUESTS.items()
        },
        in_flight_requests=dict(_IN_FLIGHT_REQUESTS),
        plan_correlation_by_id=dict(_PLAN_CORRELATION_BY_ID),
        simulation_correlation_by_id=dict(_SIMULATION_CORRELATION_BY_ID),
        metrics_by_tool=deepcopy(_TOOL_METRICS),
    )


def _restore_runtime_snapshot(snapshot: PersistenceSnapshot) -> None:
    _COMPLETED_REQUESTS.clear()
    _COMPLETED_REQUESTS.update(snapshot.completed_requests)
    _IN_FLIGHT_REQUESTS.clear()
    _IN_FLIGHT_REQUESTS.update(snapshot.in_flight_requests)
    _PLAN_CORRELATION_BY_ID.clear()
    _PLAN_CORRELATION_BY_ID.update(snapshot.plan_correlation_by_id)
    _SIMULATION_CORRELATION_BY_ID.clear()
    _SIMULATION_CORRELATION_BY_ID.update(snapshot.simulation_correlation_by_id)
    _TOOL_METRICS.clear()
    _TOOL_METRICS.update(deepcopy(snapshot.metrics_by_tool))


def _persist_runtime_snapshot_best_effort() -> None:
    try:
        _persist_runtime_snapshot()
    except ValueError:
        return


def _persist_runtime_snapshot() -> None:
    global _PERSISTENCE_BACKEND
    global _PERSISTENCE_BACKEND_NAME

    backend = _PERSISTENCE_BACKEND
    if backend is None:
        return

    try:
        backend.save_snapshot(_runtime_snapshot())
    except PersistenceCorruptionError as exc:
        if _allow_corruption_fallback():
            _PERSISTENCE_BACKEND = None
            _PERSISTENCE_BACKEND_NAME = "memory"
            raise ValueError(
                "SQLite persistence backend is corrupt; switched to memory backend "
                "because fallback is explicitly enabled"
            ) from exc
        raise ValueError(
            "SQLite persistence backend is corrupt; configure "
            f"{_PERSISTENCE_CORRUPTION_FALLBACK_ENV}=true to allow fallback"
        ) from exc
    except PersistenceUnavailableError as exc:
        raise ValueError("SQLite persistence backend unavailable during request") from exc


def _initialize_persistence_backend() -> None:
    global _PERSISTENCE_BACKEND
    global _PERSISTENCE_BACKEND_NAME

    backend = _configured_persistence_backend()
    if backend == "memory":
        _PERSISTENCE_BACKEND_NAME = "memory"
        _PERSISTENCE_BACKEND = None
        return

    sqlite_backend = SQLitePersistenceBackend(_configured_sqlite_path())
    try:
        snapshot = sqlite_backend.load_snapshot()
    except PersistenceCorruptionError as exc:
        if _allow_corruption_fallback():
            _PERSISTENCE_BACKEND_NAME = "memory"
            _PERSISTENCE_BACKEND = None
            return
        raise RuntimeError(
            "SQLite persistence backend is corrupt and fallback is disabled"
        ) from exc
    except PersistenceUnavailableError:
        _PERSISTENCE_BACKEND_NAME = "memory"
        _PERSISTENCE_BACKEND = None
        return

    _restore_runtime_snapshot(snapshot)
    _PERSISTENCE_BACKEND_NAME = "sqlite"
    _PERSISTENCE_BACKEND = sqlite_backend


def _idempotency_now_seconds() -> float:
    return perf_counter()


_initialize_persistence_backend()
_apply_dev_failure_injection()


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
            key=lambda req_id: (
                _COMPLETED_REQUESTS[req_id].completed_at_monotonic,
                req_id,
            ),
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
    _TELEMETRY.record(record)
    print(json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)


def telemetry_list_events(
    *,
    tool: str | None = None,
    outcome: str | None = None,
    error_code: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> Mapping[str, object]:
    return _TELEMETRY.list_events(
        tool=tool,
        outcome=outcome,
        error_code=error_code,
        page=page,
        page_size=page_size,
    )


def telemetry_invocation_detail(request_id: str) -> Mapping[str, object] | None:
    return _TELEMETRY.invocation_detail(request_id)


def telemetry_correlation_chain(correlation_id: str) -> Mapping[str, object]:
    return _TELEMETRY.correlation_chain(correlation_id)


def telemetry_overview() -> Mapping[str, object]:
    return _TELEMETRY.overview()


def telemetry_plan_runtime_metrics() -> dict[str, float]:
    bucket = _TOOL_METRICS.get(_PLAN_RUNTIME_METRIC_KEY)
    if bucket is None:
        return {"sync_calls": 0.0, "async_calls": 0.0}
    return {
        "sync_calls": float(bucket.get("sync_calls", 0.0)),
        "async_calls": float(bucket.get("async_calls", 0.0)),
    }


def telemetry_dashboard_snapshot(*, page_size: int = 20) -> Mapping[str, object]:
    return _TELEMETRY.dashboard_snapshot(
        page_size=page_size,
        persistence_backend=_PERSISTENCE_BACKEND_NAME,
        idempotency_completed_entries=len(_COMPLETED_REQUESTS),
        idempotency_in_flight_entries=len(_IN_FLIGHT_REQUESTS),
    )


def telemetry_negotiation_sessions(*, limit: int = 10) -> Mapping[str, object]:
    return _TELEMETRY.list_negotiation_sessions(limit=limit)


def telemetry_specialist_metrics() -> dict[str, dict[str, float]]:
    """Return a live snapshot of per-specialist metrics keyed by 'specialist.<name>'."""
    return {k: dict(v) for k, v in deepcopy(_TOOL_METRICS).items() if k.startswith("specialist.")}


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
    else:
        bucket["failures"] += 1.0
        if auth_failure:
            bucket["auth_failures"] += 1.0
    _persist_runtime_snapshot_best_effort()


def _record_specialist_metric(timing: SpecialistTiming) -> None:
    tool_name = f"specialist.{timing.subsystem_name}"
    bucket = _TOOL_METRICS.setdefault(tool_name, {key: 0.0 for key in _SPECIALIST_METRIC_KEYS})
    bucket["calls"] += 1.0
    bucket["total_latency_ms"] += timing.latency_ms
    if timing.failed:
        bucket["fault_count"] += 1.0
        bucket["last_failed"] = 1.0
        bucket["last_gate_accepted"] = 0.0
    else:
        bucket["last_failed"] = 0.0
        if timing.gate_accepted:
            bucket["gate_pass"] += 1.0
            bucket["last_gate_accepted"] = 1.0
        else:
            bucket["gate_fail"] += 1.0
            bucket["last_gate_accepted"] = 0.0
    bucket["last_latency_ms"] = timing.latency_ms
    _persist_runtime_snapshot_best_effort()


def _record_negotiation_session(payload: Mapping[str, object]) -> None:
    _TELEMETRY.record_negotiation_session(payload)


_adapter.planner.negotiation_observer = _record_negotiation_session


def _record_plan_runtime_metric(*, async_runtime: bool) -> None:
    bucket = _TOOL_METRICS.setdefault(
        _PLAN_RUNTIME_METRIC_KEY,
        {key: 0.0 for key in _PLAN_RUNTIME_METRIC_KEYS},
    )
    if async_runtime:
        bucket["async_calls"] += 1.0
    else:
        bucket["sync_calls"] += 1.0
    _persist_runtime_snapshot_best_effort()


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
        _optional_string(arguments, "benchmark_profile")
        return

    if tool_name == "mars.release":
        _require_string(arguments, "plan_id")
        _require_string(arguments, "simulation_id")
        release_type = _require_string(arguments, "release_type")
        if release_type not in {"quarterly", "hotfix"}:
            raise ValueError("Argument 'release_type' must be either 'quarterly' or 'hotfix'")
        _require_string(arguments, "version")
        _require_string_sequence(arguments, "changed_doc_ids")
        _require_string(arguments, "summary")
        _optional_string(arguments, "benchmark_profile")
        min_confidence = _optional_float(arguments, "min_confidence", 0.85)
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("Argument 'min_confidence' must be between 0.0 and 1.0")
        _optional_string(arguments, "output_dir")
        return

    if tool_name == "mars.telemetry.overview":
        return

    if tool_name == "mars.telemetry.events":
        if "tool" in arguments and arguments["tool"] is not None:
            _require_string(arguments, "tool")
        if "outcome" in arguments and arguments["outcome"] is not None:
            _require_string(arguments, "outcome")
        if "error_code" in arguments and arguments["error_code"] is not None:
            _require_string(arguments, "error_code")
        page = _optional_int(arguments, "page", 1)
        page_size = _optional_int(arguments, "page_size", 50)
        if page <= 0:
            raise ValueError("Argument 'page' must be greater than zero")
        if page_size <= 0:
            raise ValueError("Argument 'page_size' must be greater than zero")
        return

    if tool_name == "mars.telemetry.invocation":
        _require_string(arguments, "invocation_request_id")
        return

    if tool_name == "mars.telemetry.correlation":
        _require_string(arguments, "correlation_id")
        return

    if tool_name == "mars.telemetry.dashboard":
        page_size = _optional_int(arguments, "page_size", 20)
        if page_size <= 0:
            raise ValueError("Argument 'page_size' must be greater than zero")
        return

    raise KeyError(f"Unsupported MCP tool: {tool_name}")


def _prepare_idempotent_request(
    request_id: str,
    tool_name: str,
    fingerprint: str,
) -> dict[str, object] | None:
    snapshot_before = _runtime_snapshot()
    _prune_completed_idempotency_entries()
    try:
        _persist_runtime_snapshot()
    except ValueError:
        _restore_runtime_snapshot(snapshot_before)
        raise

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
    try:
        _persist_runtime_snapshot()
    except ValueError:
        _restore_runtime_snapshot(snapshot_before)
        raise
    return None


def _complete_idempotent_request(
    request_id: str,
    tool_name: str,
    fingerprint: str,
    response: dict[str, object],
) -> None:
    snapshot_before = _runtime_snapshot()
    _IN_FLIGHT_REQUESTS.pop(request_id, None)
    _COMPLETED_REQUESTS[request_id] = IdempotencyEntry(
        tool_name=tool_name,
        fingerprint=fingerprint,
        response=deepcopy(response),
        completed_at_monotonic=_idempotency_now_seconds(),
    )
    _prune_completed_idempotency_entries()
    try:
        _persist_runtime_snapshot()
    except ValueError:
        _restore_runtime_snapshot(snapshot_before)
        raise


def _reset_in_flight_request(request_id: str) -> None:
    if request_id not in _IN_FLIGHT_REQUESTS:
        return

    snapshot_before = _runtime_snapshot()
    _IN_FLIGHT_REQUESTS.pop(request_id, None)
    try:
        _persist_runtime_snapshot()
    except ValueError:
        _restore_runtime_snapshot(snapshot_before)
        raise


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


async def _run_bounded_async[TAsyncResult](
    func: Callable[[], Awaitable[TAsyncResult]],
    *,
    timeout_seconds: float | None,
) -> TAsyncResult:
    if timeout_seconds is None:
        return await func()

    try:
        with anyio.fail_after(timeout_seconds):
            return await func()
    except TimeoutError as exc:
        raise TimeoutError(
            f"Tool execution exceeded timeout of {timeout_seconds:.3f} seconds"
        ) from exc


async def _invoke_runtime_tool(
    tool_name: str,
    arguments: Mapping[str, object],
) -> dict[str, object]:
    if tool_name == "mars.telemetry.overview":
        return {
            "overview": telemetry_overview(),
            "plan_runtime": telemetry_plan_runtime_metrics(),
        }

    if tool_name == "mars.telemetry.events":
        return {
            "events": telemetry_list_events(
                tool=cast(str | None, arguments.get("tool")),
                outcome=cast(str | None, arguments.get("outcome")),
                error_code=cast(str | None, arguments.get("error_code")),
                page=_optional_int(arguments, "page", 1),
                page_size=_optional_int(arguments, "page_size", 50),
            )
        }

    if tool_name == "mars.telemetry.invocation":
        invocation_request_id = _require_string(arguments, "invocation_request_id")
        detail = telemetry_invocation_detail(invocation_request_id)
        return {"invocation": detail}

    if tool_name == "mars.telemetry.correlation":
        correlation_id = _require_string(arguments, "correlation_id")
        return {"correlation": telemetry_correlation_chain(correlation_id)}

    if tool_name == "mars.telemetry.dashboard":
        page_size = _optional_int(arguments, "page_size", 20)
        return {"dashboard": telemetry_dashboard_snapshot(page_size=page_size)}

    if tool_name == "mars.plan":
        goal = _parse_goal(_require_mapping(arguments, "goal"))
        evidence = _parse_evidence(arguments)

        if _planner_async_runtime_enabled():
            async def _compute_plan_async() -> PlanResult:
                return await _adapter.planner.plan_async(goal=goal, evidence=evidence)

            plan = cast(
                PlanResult,
                await _run_bounded_async(
                    _compute_plan_async,
                    timeout_seconds=_configured_tool_timeout_seconds(),
                ),
            )
        else:
            def _compute_plan() -> PlanResult:
                return _adapter.planner.plan(goal=goal, evidence=evidence)

            plan = await _run_bounded_sync(
                _compute_plan,
                timeout_seconds=_configured_tool_timeout_seconds(),
            )
        for timing in plan.specialist_timings:
            _record_specialist_metric(timing)
        plan_id = f"plan-{len(_adapter._plans) + 1:04d}"
        _adapter._plans[plan_id] = plan
        result: dict[str, object] = {
            "plan_id": plan_id,
            "plan": cast(object, to_mcp_value(plan)),
        }
        if plan.degraded:
            result["degraded"] = True
            result["specialist_faults"] = [
                {
                    "subsystem": t.subsystem_name,
                    "failure_reason": t.failure_reason,
                }
                for t in plan.specialist_timings
                if t.failed
            ]
        return result

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
        def _compute_benchmark() -> dict[str, object]:
            return _as_object_dict(_adapter.invoke(tool_name, arguments))

        return await _run_bounded_sync(
            _compute_benchmark,
            timeout_seconds=_configured_tool_timeout_seconds(),
        )

    if tool_name == "mars.release":
        def _compute_release() -> dict[str, object]:
            return _as_object_dict(_adapter.invoke(tool_name, arguments))

        return await _run_bounded_sync(
            _compute_release,
            timeout_seconds=_configured_tool_timeout_seconds(),
        )

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

        if tool_name == "mars.plan":
            _record_plan_runtime_metric(async_runtime=_planner_async_runtime_enabled())

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
    benchmark_profile: str | None = None,
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Evaluate benchmark deltas using plan and simulation outputs."""

    return await _invoke_with_envelope(
        tool_name="mars.benchmark",
        arguments={
            "plan_id": plan_id,
            "simulation_id": simulation_id,
            "benchmark_profile": benchmark_profile,
        },
        request_id=request_id,
        auth_token=auth_token,
    )


async def mars_release(
    plan_id: str,
    simulation_id: str,
    release_type: str,
    version: str,
    changed_doc_ids: list[str],
    summary: str,
    benchmark_profile: str | None = None,
    min_confidence: float = 0.85,
    output_dir: str | None = None,
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Finalize a governance release and optionally export a deterministic bundle."""

    return await _invoke_with_envelope(
        tool_name="mars.release",
        arguments={
            "plan_id": plan_id,
            "simulation_id": simulation_id,
            "release_type": release_type,
            "version": version,
            "changed_doc_ids": changed_doc_ids,
            "summary": summary,
            "benchmark_profile": benchmark_profile,
            "min_confidence": min_confidence,
            "output_dir": output_dir,
        },
        request_id=request_id,
        auth_token=auth_token,
    )


async def mars_telemetry_overview(
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Return telemetry health and aggregate counters."""

    return await _invoke_with_envelope(
        tool_name="mars.telemetry.overview",
        arguments={},
        request_id=request_id,
        auth_token=auth_token,
    )


async def mars_telemetry_events(
    tool: str | None = None,
    outcome: str | None = None,
    error_code: str | None = None,
    page: int = 1,
    page_size: int = 50,
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """List telemetry events with deterministic ordering, filtering, and pagination."""

    return await _invoke_with_envelope(
        tool_name="mars.telemetry.events",
        arguments={
            "tool": tool,
            "outcome": outcome,
            "error_code": error_code,
            "page": page,
            "page_size": page_size,
        },
        request_id=request_id,
        auth_token=auth_token,
    )


async def mars_telemetry_invocation(
    invocation_request_id: str,
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Return a single invocation detail view by request_id."""

    return await _invoke_with_envelope(
        tool_name="mars.telemetry.invocation",
        arguments={"invocation_request_id": invocation_request_id},
        request_id=request_id,
        auth_token=auth_token,
    )


async def mars_telemetry_correlation(
    correlation_id: str,
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Return correlation chain drill-down view by correlation_id."""

    return await _invoke_with_envelope(
        tool_name="mars.telemetry.correlation",
        arguments={"correlation_id": correlation_id},
        request_id=request_id,
        auth_token=auth_token,
    )


async def mars_telemetry_dashboard(
    page_size: int = 20,
    request_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, object]:
    """Return Week 1 mission-ops dashboard skeleton panels."""

    return await _invoke_with_envelope(
        tool_name="mars.telemetry.dashboard",
        arguments={"page_size": page_size},
        request_id=request_id,
        auth_token=auth_token,
    )


mcp.tool(name="mars.plan")(mars_plan)
mcp.tool(name="mars.simulate")(mars_simulate)
mcp.tool(name="mars.governance")(mars_governance)
mcp.tool(name="mars.benchmark")(mars_benchmark)
mcp.tool(name="mars.release")(mars_release)
mcp.tool(name="mars.telemetry.overview")(mars_telemetry_overview)
mcp.tool(name="mars.telemetry.events")(mars_telemetry_events)
mcp.tool(name="mars.telemetry.invocation")(mars_telemetry_invocation)
mcp.tool(name="mars.telemetry.correlation")(mars_telemetry_correlation)
mcp.tool(name="mars.telemetry.dashboard")(mars_telemetry_dashboard)
