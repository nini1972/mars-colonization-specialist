from datetime import date

from mars_agent.knowledge.models import TrustTier
from mars_agent.orchestration import CentralPlanner, MissionGoal, MissionPhase, PlannerSettings
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.specialists import Subsystem


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
