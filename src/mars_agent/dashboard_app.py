"""FastAPI dashboard app for Phase 9B operator workflows."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final, cast

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mcp.server.fastmcp.exceptions import ToolError

from mars_agent.governance import list_policy_profiles
from mars_agent.mcp.server import (
    _PLAN_CORRELATION_BY_ID,
    _SIMULATION_CORRELATION_BY_ID,
    _TELEMETRY,
    mars_benchmark,
    mars_release,
    mcp,
    telemetry_correlation_chain,
    telemetry_dashboard_snapshot,
    telemetry_invocation_detail,
    telemetry_list_events,
    telemetry_negotiation_sessions,
    telemetry_plan_runtime_metrics,
    telemetry_specialist_metrics,
)

_MCP_HTTP_APP = mcp.streamable_http_app()

UI_SCHEMA_VERSION: Final[str] = "1.0"

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _BASE_DIR / "web" / "templates"
_STATIC_DIR = _BASE_DIR / "web" / "static"
_WORKSPACE_ROOT = _BASE_DIR.parent.parent
_SOAK_REPORT_PATH_ENV = "MARS_DASHBOARD_SOAK_REPORT_PATH"
_DEFAULT_SOAK_REPORT_CANDIDATES: Final[tuple[Path, ...]] = (
    _WORKSPACE_ROOT / "data" / "processed" / "planner-soak-local.json",
    _WORKSPACE_ROOT / "data" / "processed" / "planner-soak-ci.json",
)

_DEMO_RECORDS: Final[list[dict[str, object]]] = [
    {
        "event": "tool.success",
        "tool": "mars.plan",
        "request_id": "demo-req-plan-1",
        "correlation_id": "demo-corr-1",
        "mission_id": "mars-demo",
        "outcome": "success",
        "latency_ms": 142.3,
        "auth_enabled": False,
        "error_code": None,
        "recorded_at": "2026-04-11T12:00:00.000+00:00",
    },
    {
        "event": "tool.success",
        "tool": "mars.simulate",
        "request_id": "demo-req-sim-1",
        "correlation_id": "demo-corr-1",
        "mission_id": "mars-demo",
        "outcome": "success",
        "latency_ms": 389.1,
        "auth_enabled": False,
        "error_code": None,
        "recorded_at": "2026-04-11T12:00:01.000+00:00",
    },
    {
        "event": "tool.success",
        "tool": "mars.governance",
        "request_id": "demo-req-gov-1",
        "correlation_id": "demo-corr-1",
        "mission_id": "mars-demo",
        "outcome": "success",
        "latency_ms": 58.7,
        "auth_enabled": False,
        "error_code": None,
        "recorded_at": "2026-04-11T12:00:02.000+00:00",
    },
    {
        "event": "tool.success",
        "tool": "mars.benchmark",
        "request_id": "demo-req-bench-1",
        "correlation_id": "demo-corr-1",
        "mission_id": "mars-demo",
        "outcome": "success",
        "latency_ms": 44.2,
        "auth_enabled": False,
        "error_code": None,
        "recorded_at": "2026-04-11T12:00:03.000+00:00",
    },
    {
        "event": "tool.error",
        "tool": "mars.plan",
        "request_id": "demo-req-plan-2",
        "correlation_id": "demo-corr-2",
        "mission_id": "mars-demo",
        "outcome": "failure",
        "latency_ms": 12.0,
        "auth_enabled": False,
        "error_code": "invalid_request",
        "recorded_at": "2026-04-11T12:00:04.000+00:00",
    },
]


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    if os.getenv("MARS_DEMO_SEED", "").lower() == "true":
        for record in _DEMO_RECORDS:
            _TELEMETRY.record(record)
    async with mcp.session_manager.run():
        yield


templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
app = FastAPI(title="Mars Mission Ops Dashboard", lifespan=_lifespan)
app.mount("/dashboard/static", StaticFiles(directory=str(_STATIC_DIR)), name="dashboard-static")
app.mount("/mcp", _MCP_HTTP_APP)

def _validate_exact_keys(
    payload: Mapping[str, object],
    *,
    required: set[str],
    allowed: set[str],
    label: str,
) -> None:
    keys = set(payload.keys())
    missing = sorted(required - keys)
    unexpected = sorted(keys - allowed)
    if missing or unexpected:
        raise ValueError(
            f"Invalid {label} payload shape: missing={missing}, unexpected={unexpected}"
        )


def _ensure_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Invalid {label}: expected mapping")
    return cast(Mapping[str, object], value)


def _coerce_dashboard_payload(page_size: int) -> dict[str, object]:
    payload = cast(dict[str, object], telemetry_dashboard_snapshot(page_size=page_size))
    _validate_exact_keys(
        payload,
        required={
            "schema_version",
            "overview_panel",
            "invocation_panel",
            "correlation_panel",
            "replay_degraded_panel",
        },
        allowed={
            "schema_version",
            "overview_panel",
            "invocation_panel",
            "correlation_panel",
            "replay_degraded_panel",
        },
        label="dashboard",
    )

    overview = _ensure_mapping(payload["overview_panel"], label="overview_panel")
    _validate_exact_keys(
        overview,
        required={"schema_version", "health", "counts"},
        allowed={"schema_version", "health", "counts"},
        label="overview_panel",
    )

    health = _ensure_mapping(overview["health"], label="health")
    _validate_exact_keys(
        health,
        required={"status", "latest_recorded_at"},
        allowed={"status", "latest_recorded_at"},
        label="health",
    )

    counts = _ensure_mapping(overview["counts"], label="counts")
    _validate_exact_keys(
        counts,
        required={"events", "successes", "failures"},
        allowed={"events", "successes", "failures"},
        label="counts",
    )

    replay = _ensure_mapping(payload["replay_degraded_panel"], label="replay_degraded_panel")
    _validate_exact_keys(
        replay,
        required={
            "persistence_backend",
            "persistence_degraded",
            "idempotency_completed_entries",
            "idempotency_in_flight_entries",
        },
        allowed={
            "persistence_backend",
            "persistence_degraded",
            "idempotency_completed_entries",
            "idempotency_in_flight_entries",
        },
        label="replay_degraded_panel",
    )

    invocations = _ensure_mapping(payload["invocation_panel"], label="invocation_panel")
    _validate_exact_keys(
        invocations,
        required={"schema_version", "items", "page", "page_size", "total", "has_next_page"},
        allowed={"schema_version", "items", "page", "page_size", "total", "has_next_page"},
        label="invocation_panel",
    )

    return payload


def _coerce_plan_runtime_metrics() -> Mapping[str, object]:
    payload = cast(Mapping[str, object], telemetry_plan_runtime_metrics())
    _validate_exact_keys(
        payload,
        required={"sync_calls", "async_calls"},
        allowed={"sync_calls", "async_calls"},
        label="plan_runtime",
    )
    return payload


def _get_invocations_fragment_data(
    *,
    page: int,
    page_size: int,
    tool: str | None,
    outcome: str | None,
    error_code: str | None,
) -> Mapping[str, object]:
    payload = telemetry_list_events(
        page=page,
        page_size=page_size,
        tool=tool,
        outcome=outcome,
        error_code=error_code,
    )
    _validate_exact_keys(
        payload,
        required={"schema_version", "items", "page", "page_size", "total", "has_next_page"},
        allowed={"schema_version", "items", "page", "page_size", "total", "has_next_page"},
        label="invocation_panel",
    )
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("Invalid invocation_panel items: expected list")
    ordered = sorted(
        items,
        key=lambda item: (
            str(cast(dict[str, object], item).get("recorded_at", "")),
            str(cast(dict[str, object], item).get("request_id", "")),
            str(cast(dict[str, object], item).get("event", "")),
        ),
        reverse=True,
    )
    normalized = dict(payload)
    normalized["items"] = ordered
    return normalized


def _phase_label_for_tool(tool_name: str) -> str:
    mapping = {
        "mars.plan": "Planning",
        "mars.simulate": "Simulation",
        "mars.governance": "Governance",
        "mars.benchmark": "Benchmarking",
    }
    if tool_name.startswith("mars.telemetry"):
        return "Operations"
    return mapping.get(tool_name, "Operations")


def _replay_badge_for_invocation(item: Mapping[str, object], event_count: int) -> str:
    outcome = str(item.get("outcome", ""))
    error_code = item.get("error_code")
    if outcome == "failure" and error_code == "invalid_request":
        return "Conflict-rejected"
    if event_count > 2 and outcome == "success":
        return "Replayed"
    return "Fresh execution"


def _invocation_detail_view_model(request_id: str) -> dict[str, object]:
    detail = telemetry_invocation_detail(request_id)
    if detail is None:
        return {
            "found": False,
            "request_id": request_id,
        }

    item_raw = detail.get("item")
    item = _ensure_mapping(item_raw, label="invocation_detail.item")
    tool_name = str(item.get("tool", "unknown"))
    correlation_id = str(item.get("correlation_id", ""))

    listing = telemetry_list_events(page=1, page_size=5000)
    items = listing.get("items")
    related_count = 0
    if isinstance(items, list):
        related_count = sum(
            1
            for event in items
            if isinstance(event, Mapping)
            and str(event.get("request_id", "")) == request_id
        )

    return {
        "found": True,
        "request_id": request_id,
        "schema_version": detail.get("schema_version"),
        "item": dict(item),
        "breadcrumb": {
            "root": "Mission Ops",
            "section": "Invocation Detail",
            "phase": _phase_label_for_tool(tool_name),
        },
        "replay_badge": _replay_badge_for_invocation(item, related_count),
        "correlation_id": correlation_id,
        "chain_nodes": [
            {
                "label": "Plan",
                "tool": "mars.plan",
                "active": tool_name == "mars.plan",
            },
            {
                "label": "Simulate",
                "tool": "mars.simulate",
                "active": tool_name == "mars.simulate",
            },
            {
                "label": "Governance",
                "tool": "mars.governance",
                "active": tool_name == "mars.governance",
            },
            {
                "label": "Benchmark",
                "tool": "mars.benchmark",
                "active": tool_name == "mars.benchmark",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Correlation chain helpers
# ---------------------------------------------------------------------------

_CORRELATION_SEQUENCE = ["mars.plan", "mars.simulate", "mars.governance", "mars.benchmark"]
_CORRELATION_INDEX: dict[str, int] = {t: i for i, t in enumerate(_CORRELATION_SEQUENCE)}
_CORRELATION_LABELS: dict[str, str] = {
    "mars.plan": "Plan",
    "mars.simulate": "Simulate",
    "mars.governance": "Governance",
    "mars.benchmark": "Benchmark",
}


def _extract_ordered_chain_items(chain: Mapping[str, object]) -> list[dict[str, object]]:
    items_raw = chain.get("items")
    if not isinstance(items_raw, list):
        raise ValueError("Invalid correlation chain items: expected list")
    items = [dict(_ensure_mapping(item, label="correlation_chain.item")) for item in items_raw]
    return sorted(
        items,
        key=lambda item: (
            str(item.get("recorded_at", "")),
            str(item.get("request_id", "")),
            str(item.get("event", "")),
        ),
    )


def _check_sequence_bounds(recognized: list[str]) -> list[str]:
    """Return missing_upstream / missing_downstream issues."""
    issues: list[str] = []
    if recognized[0] != "mars.plan":
        issues.append("missing_upstream")
    if recognized[-1] != "mars.benchmark":
        issues.append("missing_downstream")
    return issues


def _check_sequence_order(recognized: list[str]) -> list[str]:
    """Return order_violation if tools appear out of expected sequence order."""
    has_violation = any(
        _CORRELATION_INDEX[recognized[i]] < _CORRELATION_INDEX[recognized[i - 1]]
        for i in range(1, len(recognized))
    )
    return ["order_violation"] if has_violation else []


def _simulate_missing_issues(present: set[str]) -> list[str]:
    """Return orphaned_child / missing_node when simulate is absent but later steps exist."""
    if "mars.simulate" in present:
        return []
    issues: list[str] = []
    downstream = {"mars.governance", "mars.benchmark"} & present
    if downstream:
        issues.append("missing_node")
        issues.append("orphaned_child")
    return issues


def _check_cycle(recognized: list[str], ordered: list[dict[str, object]]) -> bool:
    """True if any tool's first event timestamp precedes the previous tool's timestamp."""
    recognized_ts = [
        str(next(it.get("recorded_at", "") for it in ordered if str(it.get("tool", "")) == t))
        for t in recognized
    ]
    return any(recognized_ts[i] < recognized_ts[i - 1] for i in range(1, len(recognized_ts)))


def _chain_integrity_issues(
    recognized: list[str],
    ordered: list[dict[str, object]],
    present: set[str],
) -> list[str]:
    if not recognized:
        return ["empty_chain"]
    issues = [
        *_check_sequence_bounds(recognized),
        *_check_sequence_order(recognized),
        *_simulate_missing_issues(present),
    ]
    if _check_cycle(recognized, ordered):
        issues.append("cycle")
    return issues


def _build_agents_panel() -> list[dict[str, object]]:
    """Build per-specialist health rows from live metric snapshot."""
    raw = telemetry_specialist_metrics()
    rows: list[dict[str, object]] = []
    for tool_name, metrics in sorted(raw.items()):
        subsystem = tool_name[len("specialist."):]
        display_name = subsystem.replace("_", " ").title()
        calls = int(metrics.get("calls", 0.0))
        total_latency = metrics.get("total_latency_ms", 0.0)
        avg_latency = round(total_latency / calls, 1) if calls > 0 else 0.0
        fault_count = int(metrics.get("fault_count", 0.0))
        last_failed = bool(metrics.get("last_failed", 0.0))
        rows.append(
            {
                "subsystem": subsystem,
                "display_name": display_name,
                "calls": calls,
                "avg_latency_ms": avg_latency,
                "gate_pass": int(metrics.get("gate_pass", 0.0)),
                "gate_fail": int(metrics.get("gate_fail", 0.0)),
                "last_gate_accepted": bool(metrics.get("last_gate_accepted", 0.0)),
                "fault_count": fault_count,
                "last_failed": last_failed,
            }
        )
    return rows


def _build_benchmark_profiles_panel() -> dict[str, object]:
    """Build operator-facing benchmark policy catalog view model."""

    raw_profiles = list_policy_profiles()
    profiles = [dict(profile) for profile in raw_profiles]
    launch_context = _latest_operator_launch_context()
    total_references = 0
    for profile in profiles:
        reference_count = profile.get("reference_count", 0)
        if isinstance(reference_count, int):
            total_references += reference_count
    default_profile = next(
        str(profile["name"]) for profile in profiles if profile.get("is_default") is True
    )
    return {
        "default_profile": default_profile,
        "profiles": profiles,
        "profile_count": len(profiles),
        "total_references": total_references,
        "launch_context": launch_context,
    }


def _latest_correlated_identifier(
    index: Mapping[str, str],
    correlation_id: str,
) -> str | None:
    matches = sorted(identifier for identifier, mapped in index.items() if mapped == correlation_id)
    if not matches:
        return None
    return matches[-1]


def _latest_operator_launch_context() -> dict[str, str] | None:
    payload = telemetry_list_events(page=1, page_size=5000, outcome="success")
    items = payload.get("items")
    if not isinstance(items, list):
        return None

    ordered = sorted(
        (item for item in items if isinstance(item, Mapping)),
        key=lambda item: (
            str(item.get("recorded_at", "")),
            str(item.get("request_id", "")),
            str(item.get("event", "")),
        ),
        reverse=True,
    )
    for item in ordered:
        tool_name = str(item.get("tool", ""))
        if tool_name not in {"mars.release", "mars.benchmark", "mars.governance", "mars.simulate"}:
            continue
        correlation_id = str(item.get("correlation_id", "")).strip()
        if not correlation_id:
            continue
        plan_id = _latest_correlated_identifier(_PLAN_CORRELATION_BY_ID, correlation_id)
        simulation_id = _latest_correlated_identifier(_SIMULATION_CORRELATION_BY_ID, correlation_id)
        if plan_id is None or simulation_id is None:
            continue
        return {
            "plan_id": plan_id,
            "simulation_id": simulation_id,
            "correlation_id": correlation_id,
            "request_id": str(item.get("request_id", "")),
            "source_tool": tool_name,
            "recorded_at": str(item.get("recorded_at", "")),
        }
    return None


def _normalize_optional_text(value: str) -> str | None:
    trimmed = value.strip()
    return trimmed or None


def _split_changed_doc_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _decode_tool_error(exc: ToolError) -> tuple[str | None, str]:
    raw = str(exc)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None, raw
    if not isinstance(payload, Mapping):
        return None, raw
    error_code = payload.get("code")
    message = payload.get("message")
    resolved_code = str(error_code) if error_code is not None else None
    resolved_message = str(message) if message is not None else raw
    return resolved_code, resolved_message


def _extract_release_success_highlights(payload: Mapping[str, object]) -> list[dict[str, str]]:
    highlights: list[dict[str, str]] = []

    release_raw = payload.get("release")
    if isinstance(release_raw, Mapping):
        manifest_raw = release_raw.get("manifest")
        if isinstance(manifest_raw, Mapping):
            version = manifest_raw.get("version")
            if isinstance(version, str) and version:
                highlights.append({"label": "Manifest Version", "value": version})

    bundle_paths_raw = payload.get("bundle_paths")
    if isinstance(bundle_paths_raw, Mapping):
        for key, label in (
            ("manifest_path", "Manifest Path"),
            ("manifest", "Manifest Path"),
            ("bulletin_path", "Bulletin Path"),
            ("bulletin", "Bulletin Path"),
            ("audit_path", "Audit Path"),
            ("audit", "Audit Path"),
        ):
            path_value = bundle_paths_raw.get(key)
            if isinstance(path_value, str) and path_value:
                highlights.append({"label": label, "value": path_value.replace("\\", "/")})

    return highlights


def _render_operator_action_result(
    request: Request,
    *,
    title: str,
    tool_name: str,
    ok: bool,
    summary: str,
    details: list[dict[str, str]],
    payload: Mapping[str, object],
    highlights: list[dict[str, str]] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="fragments/operator_launch_result.html",
        status_code=status_code,
        context={
            "ui_schema_version": UI_SCHEMA_VERSION,
            "result": {
                "title": title,
                "tool_name": tool_name,
                "ok": ok,
                "summary": summary,
                "highlights": highlights or [],
                "details": details,
                "json_body": json.dumps(payload, indent=2, sort_keys=True, default=str),
            },
        },
    )


def _resolve_soak_report_path() -> Path | None:
    configured = os.getenv(_SOAK_REPORT_PATH_ENV)
    if configured:
        return Path(configured)

    existing = [path for path in _DEFAULT_SOAK_REPORT_CANDIDATES if path.exists()]
    if not existing:
        return None

    return max(existing, key=lambda path: path.stat().st_mtime)


def _read_soak_payload(path: Path) -> Mapping[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("invalid soak payload: expected mapping")
    _validate_exact_keys(
        payload,
        required={"schema_version", "config", "results"},
        allowed={
            "schema_version",
            "config",
            "results",
            "telemetry_overview",
            "plan_runtime_metrics",
        },
        label="soak_report",
    )
    config = _ensure_mapping(payload["config"], label="soak_report.config")
    _validate_exact_keys(
        config,
        required={
            "mode",
            "requests",
            "concurrency",
            "timeout_seconds",
            "persistence_backend",
            "sqlite_path",
            "reset_runtime_state",
        },
        allowed={
            "mode",
            "requests",
            "concurrency",
            "timeout_seconds",
            "persistence_backend",
            "sqlite_path",
            "reset_runtime_state",
        },
        label="soak_report.config",
    )
    results = payload["results"]
    if not isinstance(results, list):
        raise ValueError("invalid soak payload: results must be a list")
    for item in results:
        entry = _ensure_mapping(item, label="soak_report.results.item")
        _validate_exact_keys(
            entry,
            required={
                "mode",
                "requests",
                "concurrency",
                "elapsed_seconds",
                "throughput_rps",
                "successes",
                "failures",
                "error_rate",
                "degraded_count",
                "degraded_rate",
                "latency_ms",
                "runtime_counter_delta",
                "error_types",
            },
            allowed={
                "mode",
                "requests",
                "concurrency",
                "elapsed_seconds",
                "throughput_rps",
                "successes",
                "failures",
                "error_rate",
                "degraded_count",
                "degraded_rate",
                "latency_ms",
                "runtime_counter_delta",
                "error_types",
            },
            label="soak_report.results.item",
        )
    return payload


def _build_planner_soak_overview() -> dict[str, object]:
    report_path = _resolve_soak_report_path()
    if report_path is None:
        return {
            "status": "unavailable",
            "reason": "No soak report artifact found yet.",
            "report_path": None,
        }

    if not report_path.exists():
        return {
            "status": "unavailable",
            "reason": f"Configured soak report path does not exist: {report_path}",
            "report_path": str(report_path),
        }

    try:
        payload = _read_soak_payload(report_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid",
            "reason": str(exc),
            "report_path": str(report_path),
        }

    config = _ensure_mapping(payload["config"], label="soak_report.config")
    results = cast(list[Mapping[str, object]], payload["results"])
    by_mode = {
        str(item.get("mode", "")): item
        for item in results
        if isinstance(item, Mapping)
    }
    sync_result = by_mode.get("sync")
    async_result = by_mode.get("async")

    def _metric(source: Mapping[str, object] | None, key: str) -> object:
        if source is None:
            return "n/a"
        return source.get(key, "n/a")

    def _latency(source: Mapping[str, object] | None, key: str) -> object:
        if source is None:
            return "n/a"
        latency = source.get("latency_ms")
        if not isinstance(latency, Mapping):
            return "n/a"
        return latency.get(key, "n/a")

    return {
        "status": "available",
        "schema_version": payload.get("schema_version"),
        "report_path": str(report_path),
        "last_modified": report_path.stat().st_mtime,
        "config": dict(config),
        "sync": {
            "throughput_rps": _metric(sync_result, "throughput_rps"),
            "error_rate": _metric(sync_result, "error_rate"),
            "degraded_rate": _metric(sync_result, "degraded_rate"),
            "p95_ms": _latency(sync_result, "p95"),
            "p99_ms": _latency(sync_result, "p99"),
        },
        "async": {
            "throughput_rps": _metric(async_result, "throughput_rps"),
            "error_rate": _metric(async_result, "error_rate"),
            "degraded_rate": _metric(async_result, "degraded_rate"),
            "p95_ms": _latency(async_result, "p95"),
            "p99_ms": _latency(async_result, "p99"),
        },
    }


def _build_negotiation_panel(*, limit: int = 5) -> dict[str, object]:
    payload = _ensure_mapping(
        telemetry_negotiation_sessions(limit=limit),
        label="negotiation_sessions",
    )
    _validate_exact_keys(
        payload,
        required={"schema_version", "items"},
        allowed={"schema_version", "items"},
        label="negotiation_sessions",
    )
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Invalid negotiation_sessions items: expected list")

    items: list[dict[str, object]] = []
    for raw_item in raw_items:
        item = dict(_ensure_mapping(raw_item, label="negotiation_sessions.item"))
        messages_raw = item.get("messages", [])
        proposals_raw = item.get("proposals", [])
        counter_proposals_raw = item.get("counter_proposals", [])
        messages = messages_raw if isinstance(messages_raw, list) else []
        proposals = proposals_raw if isinstance(proposals_raw, list) else []
        counter_proposals = (
            counter_proposals_raw if isinstance(counter_proposals_raw, list) else []
        )
        decision = _ensure_mapping(item.get("decision", {}), label="negotiation_sessions.decision")
        items.append(
            {
                "session_id": item.get("session_id", ""),
                "round_id": item.get("round_id", ""),
                "recorded_at": item.get("recorded_at"),
                "mission_id": item.get("mission_id", ""),
                "current_phase": item.get("current_phase", ""),
                "current_reduction": item.get("current_reduction", 0.0),
                "conflict_ids": item.get("conflict_ids", []),
                "decision": dict(decision),
                "proposals": proposals,
                "counter_proposals": counter_proposals,
                "messages": messages,
                "message_count": len(messages),
                "proposal_count": len(proposals) + len(counter_proposals),
            }
        )

    return {
        "schema_version": payload.get("schema_version"),
        "items": items,
    }


def _build_dag_nodes(
    present: set[str],
    ordered: list[dict[str, object]],
) -> list[dict[str, object]]:
    events_by_tool: dict[str, list[dict[str, object]]] = {}
    for item in ordered:
        t = str(item.get("tool", ""))
        if t in _CORRELATION_INDEX:
            events_by_tool.setdefault(t, []).append(item)
    nodes: list[dict[str, object]] = []
    for tool in _CORRELATION_SEQUENCE:
        tool_events = events_by_tool.get(tool, [])
        last_event = tool_events[-1] if tool_events else {}
        if tool not in present:
            node_status = "missing"
        elif tool in ("mars.governance", "mars.benchmark") and "mars.simulate" not in present:
            node_status = "orphaned"
        else:
            node_status = "present"
        nodes.append(
            {
                "tool": tool,
                "label": _CORRELATION_LABELS[tool],
                "status": node_status,
                "event_count": len(tool_events),
                "latest_at": last_event.get("recorded_at"),
                "latest_outcome": last_event.get("outcome"),
            }
        )
    return nodes


def _correlation_view_model(correlation_id: str) -> dict[str, object]:
    chain_raw = telemetry_correlation_chain(correlation_id)
    chain = _ensure_mapping(chain_raw, label="correlation_chain")
    _validate_exact_keys(
        chain,
        required={"schema_version", "correlation_id", "items"},
        allowed={"schema_version", "correlation_id", "items"},
        label="correlation_chain",
    )

    ordered = _extract_ordered_chain_items(chain)
    tool_sequence = [str(item.get("tool", "")) for item in ordered if str(item.get("tool", ""))]
    recognized = [tool for tool in tool_sequence if tool in _CORRELATION_INDEX]
    present = set(recognized)

    issues = _chain_integrity_issues(recognized, ordered, present)
    integrity_status = "complete" if not issues else "partial"
    return {
        "schema_version": chain.get("schema_version"),
        "correlation_id": chain.get("correlation_id", correlation_id),
        "items": ordered,
        "nodes": _build_dag_nodes(present, ordered),
        "integrity": {
            "status": integrity_status,
            "issues": sorted(set(issues)),
            "warning": (
                "Chain appears incomplete. Validate upstream/downstream steps before action."
                if issues
                else None
            ),
        },
    }


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(
    request: Request,
    page_size: int = Query(default=20, ge=1, le=200),
) -> HTMLResponse:
    try:
        payload = _coerce_dashboard_payload(page_size)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    replay = cast(Mapping[str, object], payload["replay_degraded_panel"])
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "ui_schema_version": UI_SCHEMA_VERSION,
            "dashboard": payload,
            "degraded": bool(replay.get("persistence_degraded", False)),
        },
    )


@app.get("/api/telemetry/dashboard")
def dashboard_json(
    page_size: int = Query(default=20, ge=1, le=200),
) -> dict[str, object]:
    try:
        payload = _coerce_dashboard_payload(page_size)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ui_schema_version": UI_SCHEMA_VERSION,
        "dashboard": payload,
    }


@app.get("/dashboard/fragments/overview", response_class=HTMLResponse)
def overview_fragment(
    request: Request,
    page_size: int = Query(default=20, ge=1, le=200),
) -> HTMLResponse:
    try:
        payload = _coerce_dashboard_payload(page_size)
        plan_runtime = _coerce_plan_runtime_metrics()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    replay = cast(Mapping[str, object], payload["replay_degraded_panel"])
    return templates.TemplateResponse(
        request=request,
        name="fragments/overview.html",
        context={
            "ui_schema_version": UI_SCHEMA_VERSION,
            "overview": payload["overview_panel"],
            "plan_runtime": plan_runtime,
            "replay": payload["replay_degraded_panel"],
            "degraded": bool(replay.get("persistence_degraded", False)),
        },
    )


@app.get("/dashboard/fragments/invocations", response_class=HTMLResponse)
def invocations_fragment(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    tool: str | None = None,
    outcome: str | None = None,
    error_code: str | None = None,
) -> HTMLResponse:
    tool = tool or None
    outcome = outcome or None
    error_code = error_code or None
    try:
        invocations = _get_invocations_fragment_data(
            page=page,
            page_size=page_size,
            tool=tool,
            outcome=outcome,
            error_code=error_code,
        )
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return templates.TemplateResponse(
        request=request,
        name="fragments/invocations.html",
        context={
            "invocations": invocations,
            "query": {
                "tool": tool or "",
                "outcome": outcome or "",
                "error_code": error_code or "",
            },
        },
    )


@app.get("/dashboard/fragments/invocation-detail", response_class=HTMLResponse)
def invocation_detail_fragment(request: Request, request_id: str) -> HTMLResponse:
    detail = _invocation_detail_view_model(request_id)
    return templates.TemplateResponse(
        request=request,
        name="fragments/invocation_detail.html",
        context={"detail": detail, "request_id": request_id},
    )


@app.get("/dashboard/fragments/correlation", response_class=HTMLResponse)
def correlation_fragment(request: Request, correlation_id: str) -> HTMLResponse:
    try:
        chain = _correlation_view_model(correlation_id)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request=request,
        name="fragments/correlation.html",
        context={"chain": chain, "correlation_id": correlation_id},
    )


@app.get("/dashboard/fragments/replay", response_class=HTMLResponse)
def replay_fragment(
    request: Request,
    page_size: int = Query(default=20, ge=1, le=200),
) -> HTMLResponse:
    try:
        payload = _coerce_dashboard_payload(page_size)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    replay = cast(Mapping[str, object], payload["replay_degraded_panel"])
    return templates.TemplateResponse(
        request=request,
        name="fragments/replay.html",
        context={
            "replay": payload["replay_degraded_panel"],
            "degraded": bool(replay.get("persistence_degraded", False)),
        },
    )


@app.get("/dashboard/fragments/agents", response_class=HTMLResponse)
def agents_fragment(request: Request) -> HTMLResponse:
    rows = _build_agents_panel()
    return templates.TemplateResponse(
        request=request,
        name="fragments/agents.html",
        context={
            "ui_schema_version": UI_SCHEMA_VERSION,
            "agents": rows,
        },
    )


@app.get("/dashboard/fragments/benchmark-profiles", response_class=HTMLResponse)
def benchmark_profiles_fragment(request: Request) -> HTMLResponse:
    profiles = _build_benchmark_profiles_panel()
    return templates.TemplateResponse(
        request=request,
        name="fragments/benchmark_profiles.html",
        context={
            "ui_schema_version": UI_SCHEMA_VERSION,
            "catalog": profiles,
        },
    )


@app.post("/dashboard/fragments/benchmark-launch", response_class=HTMLResponse)
async def benchmark_launch_fragment(
    request: Request,
    plan_id: str = Form(...),
    simulation_id: str = Form(...),
    benchmark_profile: str = Form(""),
    request_id: str = Form(""),
) -> HTMLResponse:
    selected_profile = _normalize_optional_text(benchmark_profile)
    resolved_request_id = _normalize_optional_text(request_id)
    try:
        response = await mars_benchmark(
            plan_id=plan_id,
            simulation_id=simulation_id,
            benchmark_profile=selected_profile,
            request_id=resolved_request_id,
        )
    except ToolError as exc:
        error_code, error_message = _decode_tool_error(exc)
        return _render_operator_action_result(
            request,
            title="Benchmark launch failed",
            tool_name="mars.benchmark",
            ok=False,
            summary=error_message,
            details=[
                {"label": "Plan ID", "value": plan_id},
                {"label": "Simulation ID", "value": simulation_id},
                {"label": "Benchmark Profile", "value": selected_profile or "default"},
                {"label": "Error Code", "value": error_code or "tool_error"},
            ],
            payload={
                "plan_id": plan_id,
                "simulation_id": simulation_id,
                "benchmark_profile": selected_profile,
                "error_code": error_code,
                "message": error_message,
            },
            status_code=400,
        )

    resolved_response = dict(response)
    resolved_request = response.get("request_id")
    details = [
        {"label": "Plan ID", "value": plan_id},
        {"label": "Simulation ID", "value": simulation_id},
        {"label": "Benchmark Profile", "value": selected_profile or "default"},
    ]
    if isinstance(resolved_request, str) and resolved_request:
        details.append({"label": "Request ID", "value": resolved_request})
    return _render_operator_action_result(
        request,
        title="Benchmark launch completed",
        tool_name="mars.benchmark",
        ok=True,
        summary="Runtime benchmark evaluation finished with the selected policy profile.",
        details=details,
        payload=resolved_response,
        highlights=None,
    )


@app.post("/dashboard/fragments/release-launch", response_class=HTMLResponse)
async def release_launch_fragment(
    request: Request,
    plan_id: str = Form(...),
    simulation_id: str = Form(...),
    release_type: str = Form("hotfix"),
    version: str = Form(""),
    changed_doc_ids: str = Form(""),
    summary: str = Form(""),
    benchmark_profile: str = Form(""),
    min_confidence: float = Form(0.85),
    output_dir: str = Form(""),
    request_id: str = Form(""),
) -> HTMLResponse:
    normalized_release_type = release_type.strip()
    if normalized_release_type not in {"hotfix", "quarterly"}:
        return _render_operator_action_result(
            request,
            title="Release launch failed",
            tool_name="mars.release",
            ok=False,
            summary="Release type must be either 'hotfix' or 'quarterly'.",
            details=[
                {"label": "Plan ID", "value": plan_id},
                {"label": "Simulation ID", "value": simulation_id},
                {"label": "Release Type", "value": normalized_release_type or "missing"},
            ],
            payload={"release_type": normalized_release_type},
            status_code=400,
        )

    normalized_version = version.strip()
    normalized_summary = summary.strip()
    parsed_doc_ids = _split_changed_doc_ids(changed_doc_ids)
    selected_profile = _normalize_optional_text(benchmark_profile)
    resolved_output_dir = _normalize_optional_text(output_dir)
    resolved_request_id = _normalize_optional_text(request_id)

    if not normalized_version:
        return _render_operator_action_result(
            request,
            title="Release launch failed",
            tool_name="mars.release",
            ok=False,
            summary="Version is required to create a release bundle.",
            details=[
                {"label": "Plan ID", "value": plan_id},
                {"label": "Simulation ID", "value": simulation_id},
                {"label": "Release Type", "value": normalized_release_type},
            ],
            payload={"version": normalized_version},
            status_code=400,
        )

    if not normalized_summary:
        return _render_operator_action_result(
            request,
            title="Release launch failed",
            tool_name="mars.release",
            ok=False,
            summary="Summary is required to create a release bundle.",
            details=[
                {"label": "Plan ID", "value": plan_id},
                {"label": "Simulation ID", "value": simulation_id},
                {"label": "Version", "value": normalized_version},
            ],
            payload={"summary": normalized_summary},
            status_code=400,
        )

    if not parsed_doc_ids:
        return _render_operator_action_result(
            request,
            title="Release launch failed",
            tool_name="mars.release",
            ok=False,
            summary="At least one changed document ID is required.",
            details=[
                {"label": "Plan ID", "value": plan_id},
                {"label": "Simulation ID", "value": simulation_id},
                {"label": "Version", "value": normalized_version},
            ],
            payload={"changed_doc_ids": parsed_doc_ids},
            status_code=400,
        )

    try:
        response = await mars_release(
            plan_id=plan_id,
            simulation_id=simulation_id,
            release_type=normalized_release_type,
            version=normalized_version,
            changed_doc_ids=parsed_doc_ids,
            summary=normalized_summary,
            benchmark_profile=selected_profile,
            min_confidence=min_confidence,
            output_dir=resolved_output_dir,
            request_id=resolved_request_id,
        )
    except ToolError as exc:
        error_code, error_message = _decode_tool_error(exc)
        return _render_operator_action_result(
            request,
            title="Release launch failed",
            tool_name="mars.release",
            ok=False,
            summary=error_message,
            details=[
                {"label": "Plan ID", "value": plan_id},
                {"label": "Simulation ID", "value": simulation_id},
                {"label": "Version", "value": normalized_version},
                {"label": "Release Type", "value": normalized_release_type},
                {"label": "Error Code", "value": error_code or "tool_error"},
            ],
            payload={
                "plan_id": plan_id,
                "simulation_id": simulation_id,
                "release_type": normalized_release_type,
                "version": normalized_version,
                "benchmark_profile": selected_profile,
                "error_code": error_code,
                "message": error_message,
            },
            status_code=400,
        )

    resolved_response = dict(response)
    resolved_request = response.get("request_id")
    details = [
        {"label": "Plan ID", "value": plan_id},
        {"label": "Simulation ID", "value": simulation_id},
        {"label": "Version", "value": normalized_version},
        {"label": "Release Type", "value": normalized_release_type},
        {"label": "Benchmark Profile", "value": selected_profile or "default"},
    ]
    if resolved_output_dir is not None:
        details.append({"label": "Output Directory", "value": resolved_output_dir})
    if isinstance(resolved_request, str) and resolved_request:
        details.append({"label": "Request ID", "value": resolved_request})
    highlights = _extract_release_success_highlights(resolved_response)
    return _render_operator_action_result(
        request,
        title="Release launch completed",
        tool_name="mars.release",
        ok=True,
        summary="Governance release workflow completed and returned a release bundle payload.",
        details=details,
        payload=resolved_response,
        highlights=highlights,
    )


@app.get("/dashboard/fragments/soak-overview", response_class=HTMLResponse)
def soak_overview_fragment(request: Request) -> HTMLResponse:
    soak = _build_planner_soak_overview()
    return templates.TemplateResponse(
        request=request,
        name="fragments/soak_overview.html",
        context={
            "ui_schema_version": UI_SCHEMA_VERSION,
            "soak": soak,
        },
    )


@app.get("/dashboard/fragments/negotiations", response_class=HTMLResponse)
def negotiations_fragment(request: Request) -> HTMLResponse:
    try:
        negotiations = _build_negotiation_panel()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request=request,
        name="fragments/negotiations.html",
        context={
            "ui_schema_version": UI_SCHEMA_VERSION,
            "negotiations": negotiations,
        },
    )


@app.get("/health")
def health() -> JSONResponse:
    """Liveness probe — always returns 200 when the process is alive."""
    return JSONResponse({"status": "ok"})


@app.get("/ready")
def ready() -> JSONResponse:
    """Readiness probe — returns 200 when persistence is healthy, 503 when degraded."""
    try:
        snapshot = telemetry_dashboard_snapshot()
        replay_panel = cast(Mapping[str, object], snapshot.get("replay_degraded_panel", {}))
        degraded = bool(replay_panel.get("persistence_degraded", False))
    except Exception:  # noqa: BLE001
        degraded = True
    if degraded:
        return JSONResponse({"status": "degraded"}, status_code=503)
    return JSONResponse({"status": "ready"})
