from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from mars_agent.mcp.persistence import (
    IdempotencyEntry,
    PersistenceSnapshot,
    SQLitePersistenceBackend,
)
from mars_agent.mcp.telemetry import TelemetryQueryService, TelemetryEventPayload


def _snapshot_with_telemetry(
    events: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
) -> PersistenceSnapshot:
    return PersistenceSnapshot(
        completed_requests={},
        in_flight_requests={},
        plan_correlation_by_id={},
        simulation_correlation_by_id={},
        metrics_by_tool={},
        telemetry_events=cast(list[dict[str, object]], events),
        negotiation_sessions=cast(list[dict[str, object]], sessions),
    )


def test_sqlite_persistence_round_trip_telemetry(tmp_path: Path) -> None:
    db_path = tmp_path / "telemetry-state.sqlite3"
    backend = SQLitePersistenceBackend(db_path)

    events = [
        {"recorded_at": "2026-06-08T20:00:00.000Z", "event": "start", "request_id": "req-1"},
        {"recorded_at": "2026-06-08T20:01:00.000Z", "event": "success", "request_id": "req-1"},
    ]
    sessions = [
        {"recorded_at": "2026-06-08T20:00:00.000Z", "session_id": "sess-1", "accepted": True},
        {"recorded_at": "2026-06-08T20:02:00.000Z", "session_id": "sess-2", "accepted": False},
    ]

    backend.save_snapshot(_snapshot_with_telemetry(events, sessions))

    reloaded = SQLitePersistenceBackend(db_path).load_snapshot()

    assert len(reloaded.telemetry_events) == 2
    assert reloaded.telemetry_events[0]["event"] == "start"
    assert reloaded.telemetry_events[1]["event"] == "success"

    assert len(reloaded.negotiation_sessions) == 2
    assert reloaded.negotiation_sessions[0]["session_id"] == "sess-1"
    assert reloaded.negotiation_sessions[1]["session_id"] == "sess-2"


def test_telemetry_service_state_get_and_restore() -> None:
    service = TelemetryQueryService(max_events=3)
    service.record({"event": "evt1", "request_id": "r1"})
    service.record({"event": "evt2", "request_id": "r2"})
    service.record({"event": "evt3", "request_id": "r3"})
    service.record({"event": "evt4", "request_id": "r4"})  # Evicts evt1 due to limit

    events, sessions = service.get_state()
    assert len(events) == 3
    assert events[0]["event"] == "evt2"
    assert events[1]["event"] == "evt3"
    assert events[2]["event"] == "evt4"

    # Restore state into a new service
    new_service = TelemetryQueryService(max_events=3)
    new_service.restore_state(events, sessions)

    restored_events, _ = new_service.get_state()
    assert len(restored_events) == 3
    assert restored_events[0]["event"] == "evt2"
    assert restored_events[1]["event"] == "evt3"
    assert restored_events[2]["event"] == "evt4"
