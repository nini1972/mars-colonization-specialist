from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mars_agent.mcp.persistence import (
    IdempotencyEntry,
    PersistenceCorruptionError,
    PersistenceSnapshot,
    PersistenceUnavailableError,
    SQLitePersistenceBackend,
)


def _snapshot() -> PersistenceSnapshot:
    return PersistenceSnapshot(
        completed_requests={
            "req-1": IdempotencyEntry(
                tool_name="mars.plan",
                fingerprint="fp-1",
                response={"ok": True, "request_id": "req-1"},
                completed_at_monotonic=100.0,
            ),
            "req-2": IdempotencyEntry(
                tool_name="mars.plan",
                fingerprint="fp-2",
                response={"ok": True, "request_id": "req-2"},
                completed_at_monotonic=101.0,
            ),
        },
        in_flight_requests={"req-inflight": ("mars.plan", "fp-inflight")},
        plan_correlation_by_id={"plan-0001": "corr-a"},
        simulation_correlation_by_id={"simulation-0001": "corr-a"},
        metrics_by_tool={
            "mars.plan": {
                "calls": 2.0,
                "successes": 2.0,
                "failures": 0.0,
                "auth_failures": 0.0,
                "total_latency_ms": 123.45,
            }
        },
    )


def test_sqlite_persistence_round_trip_preserves_runtime_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime-state.sqlite3"
    backend = SQLitePersistenceBackend(db_path)

    backend.save_snapshot(_snapshot())

    reloaded = SQLitePersistenceBackend(db_path).load_snapshot()
    assert set(reloaded.completed_requests) == {"req-1", "req-2"}
    assert reloaded.in_flight_requests["req-inflight"] == ("mars.plan", "fp-inflight")
    assert reloaded.plan_correlation_by_id["plan-0001"] == "corr-a"
    assert reloaded.simulation_correlation_by_id["simulation-0001"] == "corr-a"
    assert reloaded.metrics_by_tool["mars.plan"]["calls"] == pytest.approx(2.0)
    assert reloaded.metrics_by_tool["mars.plan"]["total_latency_ms"] == pytest.approx(123.45)


def test_sqlite_persistence_schema_version_guard_rejects_unknown_version(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime-state.sqlite3"
    SQLitePersistenceBackend(db_path)

    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = ?",
            ("999", "schema_version"),
        )

    with pytest.raises(PersistenceUnavailableError, match="Unsupported persistence schema version"):
        SQLitePersistenceBackend(db_path)


def test_sqlite_persistence_detects_corrupt_database(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime-state.sqlite3"
    db_path.write_text("not a sqlite db", encoding="utf-8")

    with pytest.raises(PersistenceCorruptionError):
        SQLitePersistenceBackend(db_path)
