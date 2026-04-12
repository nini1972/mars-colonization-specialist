"""Unit tests for NegotiationMemoryStore and _conflict_fingerprint."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mars_agent.orchestration.models import (
    ConflictSeverity,
    CrossDomainConflict,
    MissionGoal,
    MissionPhase,
    MitigationOption,
)
from mars_agent.orchestration.negotiation_store import (
    NegotiationMemoryStore,
    NegotiationOutcome,
    _conflict_fingerprint,
)
from mars_agent.specialists import Subsystem

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _goal() -> MissionGoal:
    return MissionGoal(
        mission_id="test-store-001",
        crew_size=10,
        horizon_years=2.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=500.0,
        battery_capacity_kwh=8000.0,
        dust_degradation_fraction=0.15,
        hours_without_sun=18.0,
    )


def _conflict(conflict_id: str) -> CrossDomainConflict:
    return CrossDomainConflict(
        conflict_id=conflict_id,
        description="test conflict",
        severity=ConflictSeverity.HIGH,
        impacted_subsystems=(Subsystem.POWER,),
        mitigations=(MitigationOption(rank=1, action="reduce load"),),
    )


def _outcome(
    fingerprint: str,
    hit_count: int = 0,
    is_fallback: bool = False,
) -> NegotiationOutcome:
    return NegotiationOutcome(
        conflict_fingerprint=fingerprint,
        isru_reduction_fraction=0.1,
        crew_reduction=0,
        dust_degradation_adjustment=0.0,
        rationale="test rationale",
        accepted=True,
        is_fallback=is_fallback,
        stored_at="2026-04-12T00:00:00+00:00",
        hit_count=hit_count,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_in_memory_round_trip() -> None:
    store = NegotiationMemoryStore()
    goal = _goal()
    conflicts = (_conflict("coupling.power_balance.shortfall"),)
    fp = _conflict_fingerprint(goal, conflicts)
    store.store(_outcome(fp))

    result = store.lookup(fp)

    assert result is not None
    assert result.conflict_fingerprint == fp
    assert result.isru_reduction_fraction == 0.1
    assert result.is_fallback is False


def test_unknown_fingerprint_returns_none() -> None:
    store = NegotiationMemoryStore()
    assert store.lookup("deadbeef" * 8) is None


def test_hit_count_increment() -> None:
    store = NegotiationMemoryStore()
    goal = _goal()
    conflicts = (_conflict("coupling.power_margin.low"),)
    fp = _conflict_fingerprint(goal, conflicts)
    store.store(_outcome(fp, hit_count=0))

    cached = store.lookup(fp)
    assert cached is not None
    store.store(
        NegotiationOutcome(
            conflict_fingerprint=cached.conflict_fingerprint,
            isru_reduction_fraction=cached.isru_reduction_fraction,
            crew_reduction=cached.crew_reduction,
            dust_degradation_adjustment=cached.dust_degradation_adjustment,
            rationale=cached.rationale,
            accepted=cached.accepted,
            is_fallback=cached.is_fallback,
            stored_at=cached.stored_at,
            hit_count=cached.hit_count + 1,
        )
    )

    updated = store.lookup(fp)
    assert updated is not None
    assert updated.hit_count == 1


def test_sqlite_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "test_store.db"
    store = NegotiationMemoryStore(db_path)
    goal = _goal()
    conflicts = (_conflict("coupling.power_balance.shortfall"),)
    fp = _conflict_fingerprint(goal, conflicts)
    store.store(_outcome(fp, is_fallback=True))

    # Reload from SQLite in a fresh store instance
    store2 = NegotiationMemoryStore(db_path)
    result = store2.lookup(fp)

    assert result is not None
    assert result.conflict_fingerprint == fp
    assert result.is_fallback is True


def test_schema_version_guard(tmp_path: Path) -> None:
    db_path = tmp_path / "old_store.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO metadata (key, value) VALUES ('schema_version', '99')")
        conn.commit()

    with pytest.raises(ValueError, match="schema version mismatch"):
        NegotiationMemoryStore(db_path)
