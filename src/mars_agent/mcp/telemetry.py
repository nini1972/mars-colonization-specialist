"""Phase 9B telemetry query layer foundations for MCP operator workflows."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, TypedDict, cast

TELEMETRY_SCHEMA_VERSION: Final[str] = "1.0"
_REDACTED: Final[str] = "[REDACTED]"
_REDACT_KEYS: Final[tuple[str, ...]] = (
    "authorization",
    "token",
    "auth_token",
    "password",
    "secret",
)

TelemetryOutcome = Literal["started", "success", "failure"]


class TelemetryEventPayload(TypedDict, total=False):
    recorded_at: str
    event: str
    tool: str
    request_id: str
    correlation_id: str
    mission_id: str | None
    outcome: TelemetryOutcome | str
    latency_ms: float
    auth_enabled: bool
    error_code: str | None
    authorization: str


class ListEventsResponse(TypedDict):
    schema_version: str
    items: list[TelemetryEventPayload]
    page: int
    page_size: int
    total: int
    has_next_page: bool


class InvocationDetailResponse(TypedDict):
    schema_version: str
    item: TelemetryEventPayload


class CorrelationChainResponse(TypedDict):
    schema_version: str
    correlation_id: str
    items: list[TelemetryEventPayload]


class OverviewHealth(TypedDict):
    status: str
    latest_recorded_at: str | None


class OverviewCounts(TypedDict):
    events: int
    successes: int
    failures: int


class OverviewResponse(TypedDict):
    schema_version: str
    health: OverviewHealth
    counts: OverviewCounts


class CorrelationPanelResponse(TypedDict):
    hint: str


class ReplayDegradedPanelResponse(TypedDict):
    persistence_backend: str
    persistence_degraded: bool
    idempotency_completed_entries: int
    idempotency_in_flight_entries: int


class DashboardSnapshotResponse(TypedDict):
    schema_version: str
    overview_panel: OverviewResponse
    invocation_panel: ListEventsResponse
    correlation_panel: CorrelationPanelResponse
    replay_degraded_panel: ReplayDegradedPanelResponse


@dataclass(slots=True)
class TelemetryEvent:
    recorded_at: str
    event: str
    tool: str
    request_id: str
    correlation_id: str
    mission_id: str | None
    outcome: str
    latency_ms: float
    auth_enabled: bool
    error_code: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "recorded_at": self.recorded_at,
            "event": self.event,
            "tool": self.tool,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "mission_id": self.mission_id,
            "outcome": self.outcome,
            "latency_ms": self.latency_ms,
            "auth_enabled": self.auth_enabled,
            "error_code": self.error_code,
        }


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds")


def _redact_value(value: object, *, key_name: str | None = None) -> object:
    lowered_key = "" if key_name is None else key_name.lower()
    if any(marker in lowered_key for marker in _REDACT_KEYS):
        return _REDACTED

    if isinstance(value, Mapping):
        return {
            str(key): _redact_value(child, key_name=str(key))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(child) for child in value]
    if isinstance(value, tuple):
        return [_redact_value(child) for child in value]
    return value


class TelemetryQueryService:
    """In-process telemetry read model with deterministic query ordering."""

    def __init__(self, *, max_events: int = 5000) -> None:
        self._events: deque[TelemetryEventPayload] = deque(maxlen=max_events)

    def record(self, payload: Mapping[str, object]) -> None:
        event_payload: dict[str, object] = {"recorded_at": _utc_now_iso()}
        for key, value in payload.items():
            event_payload[str(key)] = _redact_value(value, key_name=str(key))
        self._events.append(cast(TelemetryEventPayload, event_payload))

    def list_events(
        self,
        *,
        tool: str | None = None,
        outcome: str | None = None,
        error_code: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> ListEventsResponse:
        if page <= 0:
            raise ValueError("page must be greater than zero")
        if page_size <= 0:
            raise ValueError("page_size must be greater than zero")

        ordered = sorted(
            self._events,
            key=lambda item: (
                str(item.get("recorded_at", "")),
                str(item.get("request_id", "")),
                str(item.get("event", "")),
            ),
            reverse=True,
        )

        def _matches(item: Mapping[str, object]) -> bool:
            if tool is not None and item.get("tool") != tool:
                return False
            if outcome is not None and item.get("outcome") != outcome:
                return False
            if error_code is not None and item.get("error_code") != error_code:
                return False
            return True

        filtered = [deepcopy(item) for item in ordered if _matches(item)]
        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size

        return {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "items": filtered[start:end],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_next_page": end < total,
        }

    def invocation_detail(self, request_id: str) -> InvocationDetailResponse | None:
        ordered = sorted(
            self._events,
            key=lambda item: (
                str(item.get("recorded_at", "")),
                str(item.get("event", "")),
            ),
            reverse=True,
        )
        for item in ordered:
            if item.get("request_id") == request_id:
                return {
                    "schema_version": TELEMETRY_SCHEMA_VERSION,
                    "item": deepcopy(item),
                }
        return None

    def correlation_chain(self, correlation_id: str) -> CorrelationChainResponse:
        chain = [
            deepcopy(item)
            for item in sorted(
                self._events,
                key=lambda event: (
                    str(event.get("recorded_at", "")),
                    str(event.get("request_id", "")),
                    str(event.get("event", "")),
                ),
            )
            if item.get("correlation_id") == correlation_id
        ]
        return {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "correlation_id": correlation_id,
            "items": chain,
        }

    def overview(self) -> OverviewResponse:
        events = [deepcopy(item) for item in self._events]
        failures = sum(1 for item in events if item.get("outcome") == "failure")
        successes = sum(1 for item in events if item.get("outcome") == "success")

        latest = sorted(
            events,
            key=lambda item: (
                str(item.get("recorded_at", "")),
                str(item.get("request_id", "")),
            ),
            reverse=True,
        )
        latest_recorded_at: str | None = None
        if latest:
            candidate = latest[0].get("recorded_at")
            if isinstance(candidate, str):
                latest_recorded_at = candidate

        return {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "health": {
                "status": "degraded" if failures > 0 else "healthy",
                "latest_recorded_at": latest_recorded_at,
            },
            "counts": {
                "events": len(events),
                "successes": successes,
                "failures": failures,
            },
        }

    def dashboard_snapshot(
        self,
        *,
        page_size: int = 20,
        persistence_backend: str,
        idempotency_completed_entries: int,
        idempotency_in_flight_entries: int,
    ) -> DashboardSnapshotResponse:
        overview = self.overview()
        recent = self.list_events(page=1, page_size=page_size)
        replay_and_degraded: ReplayDegradedPanelResponse = {
            "persistence_backend": persistence_backend,
            "persistence_degraded": persistence_backend != "sqlite",
            "idempotency_completed_entries": idempotency_completed_entries,
            "idempotency_in_flight_entries": idempotency_in_flight_entries,
        }
        return {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "overview_panel": overview,
            "invocation_panel": recent,
            "correlation_panel": {
                "hint": "Use telemetry_correlation_chain(correlation_id) for drill-down",
            },
            "replay_degraded_panel": replay_and_degraded,
        }
