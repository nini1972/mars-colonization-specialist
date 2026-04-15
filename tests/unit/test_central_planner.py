from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from mars_agent.knowledge.models import TrustTier
from mars_agent.orchestration import (
    CentralPlanner,
    ConflictSeverity,
    CrossDomainConflict,
    KnowledgeContext,
    MissionGoal,
    MissionPhase,
    MitigationOption,
    PlannerSettings,
    PlanResult,
    SpecialistTiming,
)
from mars_agent.orchestration.negotiation_store import (
    NegotiationMemoryStore,
    NegotiationOutcome,
    _conflict_fingerprint,
)
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.specialists import Subsystem

if TYPE_CHECKING:
    from mars_agent.orchestration.registry import SpecialistRegistry


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-phase4-001",
            title="Phase 4 planner evidence",
            url="https://example.org/nasa-phase4-001",
            published_on=date(2026, 2, 10),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.94,
        ),
    )


def test_planner_generates_end_to_end_plan_for_large_colony() -> None:
    planner = CentralPlanner()
    goal = MissionGoal(
        mission_id="mars-colony-100",
        crew_size=100,
        horizon_years=5.0,
        current_phase=MissionPhase.LANDING,
        solar_generation_kw=1100.0,
        battery_capacity_kwh=22000.0,
        dust_degradation_fraction=0.2,
        hours_without_sun=20.0,
        desired_confidence=0.9,
    )

    result = planner.plan(goal=goal, evidence=_evidence())

    assert result.mission_id == "mars-colony-100"
    assert len(result.subsystem_responses) == 4
    assert result.next_phase in {MissionPhase.LANDING, MissionPhase.EARLY_OPERATIONS}


def test_planner_async_matches_sync_outcome_shape() -> None:
    planner = CentralPlanner()
    goal = MissionGoal(
        mission_id="mars-colony-async-shape",
        crew_size=60,
        horizon_years=4.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=900.0,
        battery_capacity_kwh=16000.0,
        dust_degradation_fraction=0.2,
        hours_without_sun=18.0,
        desired_confidence=0.9,
    )

    sync_result = planner.plan(goal=goal, evidence=_evidence())

    async def _run_async() -> PlanResult:
        return await planner.plan_async(goal=goal, evidence=_evidence())

    async_result = anyio.run(_run_async)

    assert sync_result.mission_id == async_result.mission_id
    assert sync_result.started_phase == async_result.started_phase
    assert sync_result.next_phase == async_result.next_phase
    assert len(sync_result.subsystem_responses) == len(async_result.subsystem_responses)
    assert len(sync_result.specialist_timings) == len(async_result.specialist_timings)


def test_planner_surfaces_conflicts_with_mitigations_and_replans() -> None:
    planner = CentralPlanner(
        settings=PlannerSettings(max_replan_attempts=2, replan_feedstock_reduction=0.25)
    )
    goal = MissionGoal(
        mission_id="mars-colony-constrained",
        crew_size=80,
        horizon_years=3.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=180.0,
        battery_capacity_kwh=2500.0,
        dust_degradation_fraction=0.45,
        hours_without_sun=28.0,
        desired_confidence=0.9,
    )

    result = planner.plan(goal=goal, evidence=_evidence())

    assert result.conflicts
    assert result.replan_events
    for conflict in result.conflicts:
        assert conflict.mitigations


def test_subsystem_responses_includes_thermal_response() -> None:
    planner = CentralPlanner()
    goal = MissionGoal(
        mission_id="mars-colony-thermal-check",
        crew_size=10,
        horizon_years=2.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=500.0,
        battery_capacity_kwh=8000.0,
        dust_degradation_fraction=0.15,
        hours_without_sun=18.0,
        desired_confidence=0.9,
    )

    result = planner.plan(goal=goal, evidence=_evidence())

    subsystems = {r.subsystem for r in result.subsystem_responses}
    assert Subsystem.HABITAT_THERMODYNAMICS in subsystems


def test_thermal_shortfall_conflict_present_with_severely_constrained_power() -> None:
    planner = CentralPlanner(
        settings=PlannerSettings(max_replan_attempts=0)
    )
    goal = MissionGoal(
        mission_id="mars-colony-thermal-shortfall",
        crew_size=80,
        horizon_years=2.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=50.0,
        battery_capacity_kwh=1000.0,
        dust_degradation_fraction=0.1,
        hours_without_sun=24.0,
        desired_confidence=0.9,
    )

    result = planner.plan(goal=goal, evidence=_evidence())

    conflict_ids = {c.conflict_id for c in result.conflicts}
    assert "coupling.power_balance.thermal_shortfall" in conflict_ids


# ---------------------------------------------------------------------------
# Unit tests for CentralPlanner._conflict_aware_fallback (Track B)
# ---------------------------------------------------------------------------


def _make_conflict(
    conflict_id: str, severity: ConflictSeverity = ConflictSeverity.HIGH
) -> CrossDomainConflict:
    return CrossDomainConflict(
        conflict_id=conflict_id,
        description=f"Test conflict: {conflict_id}",
        severity=severity,
        impacted_subsystems=(Subsystem.POWER,),
        mitigations=(MitigationOption(rank=1, action="reduce load"),),
    )


def _knowledge_context(weight: float = 1.0) -> KnowledgeContext:
    return KnowledgeContext(top_evidence=_evidence(), trust_weight=weight)


def test_conflict_aware_fallback_isru_only_for_power_shortfall() -> None:
    planner = CentralPlanner()
    conflicts = (_make_conflict("coupling.power_balance.shortfall"),)
    new_reduction, crew_delta, dust_delta, rationale = planner._conflict_aware_fallback(
        conflicts,
        current_reduction=0.0,
        knowledge_context=_knowledge_context(),
    )
    assert new_reduction == 0.05
    assert crew_delta == 0
    assert dust_delta == 0.0
    assert "coupling.power_balance.shortfall" in rationale


def test_conflict_aware_fallback_margin_low_applies_crew_reduction() -> None:
    planner = CentralPlanner()
    conflicts = (_make_conflict("coupling.power_margin.low", ConflictSeverity.MEDIUM),)
    new_reduction, crew_delta, dust_delta, _ = planner._conflict_aware_fallback(
        conflicts,
        current_reduction=0.1,
        knowledge_context=_knowledge_context(),
    )
    assert abs(new_reduction - 0.13) < 1e-9
    assert crew_delta == 1
    assert dust_delta == 0.0


def test_conflict_aware_fallback_combines_shortfall_and_isru_share() -> None:
    planner = CentralPlanner()
    conflicts = (
        _make_conflict("coupling.power_balance.shortfall"),
        _make_conflict("coupling.isru_power_share.high", ConflictSeverity.MEDIUM),
    )
    new_reduction, crew_delta, dust_delta, _ = planner._conflict_aware_fallback(
        conflicts,
        current_reduction=0.0,
        knowledge_context=_knowledge_context(),
    )
    # max(0.05, 0.05) = 0.05 ISRU; max(0, 0) = 0 crew; max(0.0, 0.02) = 0.02 dust
    assert new_reduction == 0.05
    assert crew_delta == 0
    assert abs(dust_delta - 0.02) < 1e-9


def test_conflict_aware_fallback_unknown_id_uses_default() -> None:
    settings = PlannerSettings(replan_feedstock_reduction=0.15)
    planner = CentralPlanner(settings=settings)
    conflicts = (_make_conflict("coupling.unknown.mystery_conflict"),)
    new_reduction, crew_delta, dust_delta, _ = planner._conflict_aware_fallback(
        conflicts,
        current_reduction=0.0,
        knowledge_context=_knowledge_context(),
    )
    assert new_reduction == 0.15
    assert crew_delta == 0
    assert dust_delta == 0.0


# ---------------------------------------------------------------------------
# Unit tests for Track D — Persistent Negotiation Memory
# ---------------------------------------------------------------------------


def _constrained_goal() -> MissionGoal:
    """Returns a goal that reliably triggers coupling conflicts + replanning."""
    return MissionGoal(
        mission_id="mars-colony-track-d",
        crew_size=80,
        horizon_years=2.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=80.0,
        battery_capacity_kwh=1000.0,
        dust_degradation_fraction=0.4,
        hours_without_sun=28.0,
        desired_confidence=0.9,
    )


def test_cache_stores_outcome_after_fallback_replan() -> None:
    """After a replan, the store holds an entry for the conflict fingerprint."""
    store = NegotiationMemoryStore()
    planner = CentralPlanner(
        settings=PlannerSettings(max_replan_attempts=1),
        negotiation_store=store,
    )
    goal = _constrained_goal()
    result = planner.plan(goal=goal, evidence=_evidence())

    # At least one replan must have occurred (conflicts expected with constrained goal)
    assert result.replan_events, "Expected at least one replan for the constrained goal"
    # One or more fingerprints must be stored
    assert store._cache, "Expected negotiation outcomes to be stored in the cache"


def test_cache_hit_produces_memory_hit_rationale() -> None:
    """When a pre-populated cache entry exists, _handle_replan returns a [memory hit] rationale."""
    goal = _constrained_goal()
    conflicts = (_make_conflict("coupling.power_balance.shortfall"),)
    context = _knowledge_context()
    fp = _conflict_fingerprint(goal, conflicts, context)

    seeded_outcome = NegotiationOutcome(
        conflict_fingerprint=fp,
        isru_reduction_fraction=0.15,
        crew_reduction=0,
        dust_degradation_adjustment=0.0,
        rationale="seeded rationale",
        accepted=True,
        is_fallback=True,
        stored_at="2026-04-12T00:00:00+00:00",
        hit_count=0,
    )
    store = NegotiationMemoryStore()
    store.store(seeded_outcome)

    planner = CentralPlanner(negotiation_store=store)
    new_reduction, _updated_goal, rationale, _ = planner._handle_replan(
        goal=goal,
        conflicts=conflicts,
        current_reduction=0.0,
        knowledge_context=context,
    )

    assert "[memory hit]" in rationale
    assert "[fallback]" in rationale
    assert "seeded rationale" in rationale
    assert new_reduction == 0.15
    # hit_count should have advanced
    cached = store.lookup(fp)
    assert cached is not None
    assert cached.hit_count == 1


def test_sqlite_store_persists_across_planner_instances(tmp_path: Path) -> None:
    """Outcomes written by one planner instance are replayed by a second."""
    db_path = tmp_path / "negotiation.db"
    goal = _constrained_goal()

    # First planner — populates the SQLite store
    planner1 = CentralPlanner(
        settings=PlannerSettings(
            max_replan_attempts=1,
            negotiation_store_path=str(db_path),
        )
    )
    result1 = planner1.plan(goal=goal, evidence=_evidence())
    assert result1.replan_events, "First run must trigger at least one replan"

    # Second planner — loads same DB; should hit the cache
    planner2 = CentralPlanner(
        settings=PlannerSettings(
            max_replan_attempts=1,
            negotiation_store_path=str(db_path),
        )
    )
    result2 = planner2.plan(goal=goal, evidence=_evidence())

    # At least one replan event in the second run should carry a [memory hit] tag
    assert any(
        "[memory hit]" in ev.reason for ev in result2.replan_events
    ), "Expected [memory hit] in second-run replan events"


def test_is_fallback_true_when_negotiator_disabled() -> None:
    """With the negotiator disabled (default in tests), stored outcome has is_fallback=True."""
    store = NegotiationMemoryStore()
    goal = _constrained_goal()
    conflicts = (_make_conflict("coupling.power_balance.shortfall"),)

    context = _knowledge_context()
    planner = CentralPlanner(negotiation_store=store)
    planner._handle_replan(
        goal=goal,
        conflicts=conflicts,
        current_reduction=0.0,
        knowledge_context=context,
    )

    fp = _conflict_fingerprint(goal, conflicts, context)
    cached = store.lookup(fp)
    assert cached is not None
    assert cached.is_fallback is True


# ---------------------------------------------------------------------------
# Unit tests for Track E — Specialist Timing Capture
# ---------------------------------------------------------------------------


def _basic_goal() -> MissionGoal:
    return MissionGoal(
        mission_id="mars-timing-check",
        crew_size=20,
        horizon_years=1.0,
        current_phase=MissionPhase.LANDING,
        solar_generation_kw=500.0,
        battery_capacity_kwh=8000.0,
        dust_degradation_fraction=0.1,
        hours_without_sun=16.0,
        desired_confidence=0.9,
    )


def test_specialist_timings_length_equals_four() -> None:
    """PlanResult.specialist_timings always contains exactly 4 entries (one per subsystem)."""
    planner = CentralPlanner()
    result = planner.plan(goal=_basic_goal(), evidence=_evidence())
    assert len(result.specialist_timings) == 4


def test_specialist_timings_positive_latency() -> None:
    """Every SpecialistTiming entry must report a positive latency_ms."""
    planner = CentralPlanner()
    result = planner.plan(goal=_basic_goal(), evidence=_evidence())
    for timing in result.specialist_timings:
        assert isinstance(timing, SpecialistTiming)
        assert timing.latency_ms > 0.0


def test_specialist_timings_gate_accepted_is_bool() -> None:
    """gate_accepted must be a Python bool (not an int or float)."""
    planner = CentralPlanner()
    result = planner.plan(goal=_basic_goal(), evidence=_evidence())
    for timing in result.specialist_timings:
        assert isinstance(timing.gate_accepted, bool)


# ---------------------------------------------------------------------------
# Unit tests for Gap #7 — Specialist Fault Isolation
# ---------------------------------------------------------------------------


def _failing_specialist(subsystem: Subsystem) -> object:
    """Return a mock specialist that raises RuntimeError on analyze()."""

    class _FailingSpecialist:
        def capabilities(self) -> object:
            from mars_agent.specialists.contracts import SpecialistCapability

            return SpecialistCapability(
                subsystem=subsystem,
                accepts_inputs=(),
                produces_metrics=(),
                tradeoff_knobs=(),
            )

        def analyze(self, request: object) -> object:
            raise RuntimeError(f"Simulated failure in {subsystem.value}")

    return _FailingSpecialist()


def _registry_with_failing(failing: Subsystem) -> SpecialistRegistry:
    """Build a registry where one specialist raises on analyze()."""
    from mars_agent.orchestration.registry import SpecialistRegistry
    from mars_agent.specialists.eclss import ECLSSSpecialist
    from mars_agent.specialists.habitat_thermodynamics import HabitatThermodynamicsSpecialist
    from mars_agent.specialists.isru import ISRUSpecialist
    from mars_agent.specialists.power import PowerSpecialist

    registry = SpecialistRegistry()
    specialists = {
        Subsystem.ECLSS: ECLSSSpecialist(),
        Subsystem.ISRU: ISRUSpecialist(),
        Subsystem.POWER: PowerSpecialist(),
        Subsystem.HABITAT_THERMODYNAMICS: HabitatThermodynamicsSpecialist(),
    }
    for sub, spec in specialists.items():
        registry.register(_failing_specialist(sub) if sub == failing else spec)  # type: ignore[arg-type]
    return registry


def test_planner_degrades_gracefully_when_eclss_raises() -> None:
    """When ECLSS raises, plan() returns degraded=True and Power is also marked failed."""
    planner = CentralPlanner(registry=_registry_with_failing(Subsystem.ECLSS))
    result = planner.plan(goal=_basic_goal(), evidence=_evidence())

    assert result.degraded is True
    by_name = {t.subsystem_name: t for t in result.specialist_timings}
    assert by_name[Subsystem.ECLSS.value].failed is True
    assert by_name[Subsystem.ECLSS.value].failure_reason is not None
    # Power depends on ECLSS — must also be marked failed (skipped)
    assert by_name[Subsystem.POWER.value].failed is True
    # ISRU and Thermal should have succeeded
    assert by_name[Subsystem.ISRU.value].failed is False
    assert by_name[Subsystem.HABITAT_THERMODYNAMICS.value].failed is False
    # Only successful responses included
    assert len(result.subsystem_responses) == 2  # ISRU + Thermal


def test_planner_degrades_gracefully_when_isru_raises() -> None:
    """When ISRU raises, plan() returns degraded=True and Power is also marked failed."""
    planner = CentralPlanner(registry=_registry_with_failing(Subsystem.ISRU))
    result = planner.plan(goal=_basic_goal(), evidence=_evidence())

    assert result.degraded is True
    by_name = {t.subsystem_name: t for t in result.specialist_timings}
    assert by_name[Subsystem.ISRU.value].failed is True
    assert by_name[Subsystem.POWER.value].failed is True
    assert by_name[Subsystem.ECLSS.value].failed is False
    assert by_name[Subsystem.HABITAT_THERMODYNAMICS.value].failed is False
    assert len(result.subsystem_responses) == 2  # ECLSS + Thermal


def test_planner_degrades_gracefully_when_thermal_only_raises() -> None:
    """When only Thermal raises, ECLSS/ISRU/Power still succeed and plan is degraded."""
    planner = CentralPlanner(registry=_registry_with_failing(Subsystem.HABITAT_THERMODYNAMICS))
    result = planner.plan(goal=_basic_goal(), evidence=_evidence())

    assert result.degraded is True
    by_name = {t.subsystem_name: t for t in result.specialist_timings}
    assert by_name[Subsystem.HABITAT_THERMODYNAMICS.value].failed is True
    # Power must still have been attempted (ECLSS + ISRU both available)
    assert by_name[Subsystem.POWER.value].failed is False
    assert by_name[Subsystem.ECLSS.value].failed is False
    assert by_name[Subsystem.ISRU.value].failed is False
    # Three successful responses: ECLSS, ISRU, Power
    assert len(result.subsystem_responses) == 3


def test_planner_degrades_gracefully_when_power_raises() -> None:
    """When Power raises, plan() returns degraded=True; ECLSS/ISRU/Thermal succeed."""
    planner = CentralPlanner(registry=_registry_with_failing(Subsystem.POWER))
    result = planner.plan(goal=_basic_goal(), evidence=_evidence())

    assert result.degraded is True
    by_name = {t.subsystem_name: t for t in result.specialist_timings}
    assert by_name[Subsystem.POWER.value].failed is True
    assert by_name[Subsystem.ECLSS.value].failed is False
    assert by_name[Subsystem.ISRU.value].failed is False
    assert by_name[Subsystem.HABITAT_THERMODYNAMICS.value].failed is False
    # Three successful responses: ECLSS, ISRU, Thermal
    assert len(result.subsystem_responses) == 3


def test_degraded_plan_stays_in_current_phase() -> None:
    """A degraded plan must not advance the mission phase (no readiness signals available)."""
    planner = CentralPlanner(registry=_registry_with_failing(Subsystem.ECLSS))
    goal = _basic_goal()
    result = planner.plan(goal=goal, evidence=_evidence())

    assert result.degraded is True
    assert result.next_phase == goal.current_phase


def test_degraded_plan_has_no_conflicts() -> None:
    """A degraded plan must report no conflicts (coupling check was skipped)."""
    planner = CentralPlanner(registry=_registry_with_failing(Subsystem.ISRU))
    result = planner.plan(goal=_basic_goal(), evidence=_evidence())

    assert result.degraded is True
    assert result.conflicts == ()


def test_healthy_plan_is_not_marked_degraded() -> None:
    """A plan with all specialists succeeding must return degraded=False."""
    planner = CentralPlanner()
    result = planner.plan(goal=_basic_goal(), evidence=_evidence())

    assert result.degraded is False
    assert all(not t.failed for t in result.specialist_timings)

