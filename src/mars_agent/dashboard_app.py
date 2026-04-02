"""FastAPI dashboard app for Phase 9B operator workflows."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mars_agent.mcp.server import (
    telemetry_correlation_chain,
    telemetry_dashboard_snapshot,
    telemetry_invocation_detail,
    telemetry_list_events,
)

UI_SCHEMA_VERSION: Final[str] = "1.0"

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _BASE_DIR / "web" / "templates"
_STATIC_DIR = _BASE_DIR / "web" / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
app = FastAPI(title="Mars Mission Ops Dashboard")
app.mount("/dashboard/static", StaticFiles(directory=str(_STATIC_DIR)), name="dashboard-static")


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
    detail = telemetry_invocation_detail(request_id)
    return templates.TemplateResponse(
        request=request,
        name="fragments/invocation_detail.html",
        context={"detail": detail, "request_id": request_id},
    )


@app.get("/dashboard/fragments/correlation", response_class=HTMLResponse)
def correlation_fragment(request: Request, correlation_id: str) -> HTMLResponse:
    chain = telemetry_correlation_chain(correlation_id)
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
