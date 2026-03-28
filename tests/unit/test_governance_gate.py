from dataclasses import replace
from datetime import date

from mars_agent.governance import GovernanceGate
from mars_agent.knowledge.models import TrustTier
from mars_agent.orchestration import CentralPlanner, MissionGoal, MissionPhase
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.simulation import SimulationPipeline


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-phase6-001",
            title="Phase 6 governance evidence",
            url="https://example.org/nasa-phase6-001",
            published_on=date(2026, 3, 5),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.96,
        ),
    )


def _nominal_goal() -> MissionGoal:
    return MissionGoal(
        mission_id="mars-phase6-nominal",
        crew_size=100,
        horizon_years=5.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=1200.0,
        battery_capacity_kwh=24000.0,
        dust_degradation_fraction=0.2,
        hours_without_sun=20.0,
    )


def test_governance_gate_accepts_safe_provenance_complete_outputs() -> None:
    plan = CentralPlanner().plan(goal=_nominal_goal(), evidence=_evidence())
    simulation = SimulationPipeline(seed=42, max_repair_attempts=2).run(plan)

    report = GovernanceGate(min_confidence=0.75).evaluate(plan=plan, simulation=simulation)

    assert report.accepted
    assert report.violations == ()


def test_governance_gate_rejects_missing_provenance() -> None:
    plan = CentralPlanner().plan(goal=_nominal_goal(), evidence=_evidence())
    simulation = SimulationPipeline(seed=42, max_repair_attempts=2).run(plan)

    original = plan.subsystem_responses[0]
    decision_without_evidence = replace(original.decision, evidence=())
    modified_response = replace(original, decision=decision_without_evidence)

    modified_plan = replace(
        plan,
        subsystem_responses=(
            modified_response,
            *plan.subsystem_responses[1:],
        ),
    )

    report = GovernanceGate(min_confidence=0.75).evaluate(plan=modified_plan, simulation=simulation)

    assert not report.accepted
    assert any(item.code == "governance.provenance.missing_evidence" for item in report.violations)
