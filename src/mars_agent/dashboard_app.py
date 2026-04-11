"""FastAPI dashboard app for Phase 9B operator workflows."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final, cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mars_agent.mcp.server import (
    _TELEMETRY,
    mcp,
    telemetry_correlation_chain,
    telemetry_dashboard_snapshot,
    telemetry_invocation_detail,
    telemetry_list_events,
)

_MCP_HTTP_APP = mcp.streamable_http_app()

UI_SCHEMA_VERSION: Final[str] = "1.0"

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _BASE_DIR / "web" / "templates"
_STATIC_DIR = _BASE_DIR / "web" / "static"

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
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    replay = cast(Mapping[str, object], payload["replay_degraded_panel"])
    return templates.TemplateResponse(
        request=request,
        name="fragments/overview.html",
        context={
            "ui_schema_version": UI_SCHEMA_VERSION,
            "overview": payload["overview_panel"],
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
