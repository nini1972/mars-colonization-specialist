from datetime import date
from pathlib import Path

from mars_agent.governance.benchmark import BenchmarkHarness
from mars_agent.governance.models import BenchmarkReference
from mars_agent.knowledge.models import TrustTier
from mars_agent.mcp.adapter import MarsMCPAdapter
from mars_agent.orchestration.models import MissionGoal, MissionPhase
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.simulation.pipeline import SimulationPipeline


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-phase5-001",
            title="AresSim MCP integration evidence",
            url="https://example.org/nasa-phase5-001",
            published_on=date(2026, 3, 1),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.96,
        ),
    )


def _goal() -> MissionGoal:
    return MissionGoal(
        mission_id="mars-aressim-mcp-nominal",
        crew_size=12,
        horizon_years=2.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=1000.0,
        battery_capacity_kwh=15000.0,
        dust_degradation_fraction=0.15,
        hours_without_sun=18.0,
        desired_confidence=0.9,
    )


def _goal_payload(goal: MissionGoal) -> dict[str, object]:
    return {
        "mission_id": goal.mission_id,
        "crew_size": goal.crew_size,
        "horizon_years": goal.horizon_years,
        "current_phase": goal.current_phase.value,
        "solar_generation_kw": goal.solar_generation_kw,
        "battery_capacity_kwh": goal.battery_capacity_kwh,
        "dust_degradation_fraction": goal.dust_degradation_fraction,
        "hours_without_sun": goal.hours_without_sun,
        "desired_confidence": goal.desired_confidence,
    }


def _evidence_payload(evidence: tuple[EvidenceReference, ...]) -> list[dict[str, object]]:
    return [
        {
            "source_id": item.source_id,
            "doc_id": item.doc_id,
            "title": item.title,
            "url": item.url,
            "published_on": item.published_on.isoformat(),
            "tier": item.tier.value,
            "relevance_score": item.relevance_score,
        }
        for item in evidence
    ]


def test_mcp_simulate_with_include_aressim_returns_physical_trajectory() -> None:
    adapter = MarsMCPAdapter()
    plan_result = adapter.invoke(
        "mars.plan",
        {
            "goal": _goal_payload(_goal()),
            "evidence": _evidence_payload(_evidence()),
        },
    )
    plan_id = str(plan_result["plan_id"])

    sim_result = adapter.invoke(
        "mars.simulate",
        {
            "plan_id": plan_id,
            "seed": 42,
            "max_repair_attempts": 2,
            "include_aressim": True,
            "aressim_duration_sols": 1,
        },
    )

    assert "simulation_id" in sim_result
    assert "simulation" in sim_result
    assert "aressim" in sim_result

    aressim_payload = sim_result["aressim"]
    assert isinstance(aressim_payload, dict)
    assert aressim_payload["total_steps"] == 24
    assert aressim_payload["duration_sols"] == 1.0
    assert aressim_payload["peak_solar_generation_kw"] > 0.0
    assert 0.0 <= aressim_payload["min_bess_soc"] <= 1.0
    assert aressim_payload["cumulative_o2_g"] > 0.0
    assert aressim_payload["cumulative_water_kg"] > 0.0

    simulation_payload = sim_result["simulation"]
    assert isinstance(simulation_payload, dict)
    assert len(simulation_payload["scenarios"]) == 5
    assert simulation_payload["aressim_trajectory"] is not None


def test_mcp_simulate_default_preserves_backward_compatibility() -> None:
    adapter = MarsMCPAdapter()
    plan_result = adapter.invoke(
        "mars.plan",
        {
            "goal": _goal_payload(_goal()),
            "evidence": _evidence_payload(_evidence()),
        },
    )
    plan_id = str(plan_result["plan_id"])

    sim_result = adapter.invoke(
        "mars.simulate",
        {
            "plan_id": plan_id,
            "seed": 42,
        },
    )

    assert "simulation_id" in sim_result
    assert "simulation" in sim_result
    assert "aressim" not in sim_result

    simulation_payload = sim_result["simulation"]
    assert isinstance(simulation_payload, dict)
    assert simulation_payload.get("aressim_trajectory") is None


def test_mcp_full_chain_with_aressim_passes_governance_and_benchmark() -> None:
    harness = BenchmarkHarness(
        policy_version="2026.03-dev",
        policy_source="Synthetic permissive benchmark bundle",
        profile="nasa-esa-mission-review-permissive",
        references=(
            BenchmarkReference(
                metric="load_margin_kw",
                target=15.0,
                tolerance=1000.0,
                source="Synthetic permissive margin reference",
            ),
            BenchmarkReference(
                metric="resource_surplus_ratio",
                target=1.2,
                tolerance=1000.0,
                source="Synthetic permissive surplus reference",
            ),
            BenchmarkReference(
                metric="storage_cover_hours",
                target=20.0,
                tolerance=1000.0,
                source="Synthetic permissive storage reference",
            ),
        ),
    )
    adapter = MarsMCPAdapter(benchmark_harness=harness)

    plan_result = adapter.invoke(
        "mars.plan",
        {
            "goal": _goal_payload(_goal()),
            "evidence": _evidence_payload(_evidence()),
        },
    )
    plan_id = str(plan_result["plan_id"])

    sim_result = adapter.invoke(
        "mars.simulate",
        {
            "plan_id": plan_id,
            "include_aressim": True,
            "aressim_duration_sols": 1,
        },
    )
    simulation_id = str(sim_result["simulation_id"])

    gov_result = adapter.invoke(
        "mars.governance",
        {
            "plan_id": plan_id,
            "simulation_id": simulation_id,
            "min_confidence": 0.75,
        },
    )
    gov_payload = gov_result["governance"]
    assert isinstance(gov_payload, dict)
    assert gov_payload["accepted"] is True

    bench_result = adapter.invoke(
        "mars.benchmark",
        {
            "plan_id": plan_id,
            "simulation_id": simulation_id,
            "benchmark_profile": "nasa-esa-mission-review-permissive",
        },
    )
    bench_payload = bench_result["benchmark"]
    assert isinstance(bench_payload, dict)
    assert bench_payload["passed"] is True


def test_simulation_pipeline_standalone_python_aressim_integration() -> None:
    from mars_agent.orchestration import CentralPlanner

    planner = CentralPlanner()
    plan = planner.plan(goal=_goal(), evidence=_evidence())

    pipeline = SimulationPipeline(seed=42, include_aressim=True, aressim_duration_sols=2)
    report = pipeline.run(plan)

    assert report.aressim_trajectory is not None
    assert report.aressim_trajectory.total_steps == 48  # 2 sols = 48 hours
    metrics = report.aressim_trajectory.to_uncertainty_metrics()
    assert "effective_generation_kw" in metrics
    assert "hvac_power_demand_kw" in metrics
    assert "isru_power_demand_kw" in metrics
