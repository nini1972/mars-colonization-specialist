from datetime import date

from mars_agent.governance import BenchmarkHarness, BenchmarkReference
from mars_agent.knowledge.models import TrustTier
from mars_agent.orchestration import CentralPlanner, MissionGoal, MissionPhase
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.simulation import SimulationPipeline


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="esa-std",
            doc_id="esa-phase6-benchmark",
            title="Phase 6 benchmark evidence",
            url="https://example.org/esa-phase6-benchmark",
            published_on=date(2026, 3, 6),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.93,
        ),
    )


def _goal() -> MissionGoal:
    return MissionGoal(
        mission_id="mars-phase6-benchmark",
        crew_size=100,
        horizon_years=5.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=1200.0,
        battery_capacity_kwh=24000.0,
        dust_degradation_fraction=0.2,
        hours_without_sun=20.0,
    )


def test_benchmark_harness_computes_metric_deltas() -> None:
    plan = CentralPlanner().plan(goal=_goal(), evidence=_evidence())
    simulation = SimulationPipeline(seed=42, max_repair_attempts=2).run(plan)

    report = BenchmarkHarness().run(plan=plan, simulation=simulation)

    assert report.deltas
    assert report.profile == "nasa-esa-mission-review"
    assert report.policy_version == "2026.03"
    assert any(item.metric == "load_margin_kw" for item in report.deltas)


def test_benchmark_harness_can_fail_on_tight_tolerance() -> None:
    plan = CentralPlanner().plan(goal=_goal(), evidence=_evidence())
    simulation = SimulationPipeline(seed=42, max_repair_attempts=2).run(plan)

    strict = BenchmarkHarness(
        references=(
            BenchmarkReference(
                metric="load_margin_kw",
                target=9999.0,
                tolerance=0.01,
                source="Synthetic strict reference",
            ),
        )
    )

    report = strict.run(plan=plan, simulation=simulation)

    assert not report.passed
    assert report.policy_source == "NASA/ESA mission review benchmark bundle"
    assert report.deltas[0].within_tolerance is False
