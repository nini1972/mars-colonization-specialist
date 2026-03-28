from datetime import date

from mars_agent.knowledge.models import TrustTier
from mars_agent.orchestration import CentralPlanner, MissionGoal, MissionPhase, PlannerSettings
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.simulation import SimulationPipeline


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-phase5-001",
            title="Phase 5 simulation evidence",
            url="https://example.org/nasa-phase5-001",
            published_on=date(2026, 3, 1),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.96,
        ),
    )


def _build_plan(constrained: bool) -> MissionGoal:
    if constrained:
        return MissionGoal(
            mission_id="mars-phase5-constrained",
            crew_size=100,
            horizon_years=5.0,
            current_phase=MissionPhase.EARLY_OPERATIONS,
            solar_generation_kw=260.0,
            battery_capacity_kwh=3200.0,
            dust_degradation_fraction=0.5,
            hours_without_sun=30.0,
        )

    return MissionGoal(
        mission_id="mars-phase5-nominal",
        crew_size=100,
        horizon_years=5.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=1200.0,
        battery_capacity_kwh=24000.0,
        dust_degradation_fraction=0.2,
        hours_without_sun=20.0,
    )


def test_pipeline_covers_required_scenarios_and_is_deterministic() -> None:
    planner = CentralPlanner(settings=PlannerSettings(max_replan_attempts=2))
    plan = planner.plan(goal=_build_plan(constrained=False), evidence=_evidence())

    pipeline = SimulationPipeline(seed=42, max_repair_attempts=2)
    report_a = pipeline.run(plan)
    report_b = pipeline.run(plan)

    names = [item.scenario_name for item in report_a.scenarios]
    assert names == [
        "nominal",
        "dust_storm",
        "equipment_failure",
        "resupply_delay",
        "medical_emergency",
    ]

    assert report_a.seed == report_b.seed == 42
    assert report_a.attempts == report_b.attempts
    outputs_a = [item.outputs for item in report_a.scenarios]
    outputs_b = [item.outputs for item in report_b.scenarios]
    assert outputs_a == outputs_b


def test_pipeline_returns_actionable_remediation_for_failed_scenarios() -> None:
    planner = CentralPlanner(settings=PlannerSettings(max_replan_attempts=1))
    plan = planner.plan(goal=_build_plan(constrained=True), evidence=_evidence())

    report = SimulationPipeline(seed=42, max_repair_attempts=1).run(plan)

    failed = [item for item in report.scenarios if not item.passed]
    assert failed
    assert any(item.remediation for item in failed)
