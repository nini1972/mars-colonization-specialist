from collections.abc import Sequence
from datetime import date

from mars_agent.governance import BenchmarkHarness, GovernanceGate
from mars_agent.knowledge.models import TrustTier
from mars_agent.mcp import MarsMCPAdapter, to_mcp_value
from mars_agent.orchestration import CentralPlanner, MissionGoal, MissionPhase
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.simulation import SimulationPipeline


def _goal() -> MissionGoal:
    return MissionGoal(
        mission_id="mars-phase7-contract",
        crew_size=100,
        horizon_years=5.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=1200.0,
        battery_capacity_kwh=24000.0,
        dust_degradation_fraction=0.2,
        hours_without_sun=20.0,
        desired_confidence=0.9,
    )


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-phase7-001",
            title="Phase 7 MCP contract evidence",
            url="https://example.org/nasa-phase7-001",
            published_on=date(2026, 3, 10),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.97,
        ),
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
    payload: list[dict[str, object]] = []
    for item in evidence:
        payload.append(
            {
                "source_id": item.source_id,
                "doc_id": item.doc_id,
                "title": item.title,
                "url": item.url,
                "published_on": item.published_on.isoformat(),
                "tier": int(item.tier),
                "relevance_score": item.relevance_score,
            }
        )
    return payload


def _strip_created_at(value: object) -> object:
    if isinstance(value, dict):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if key == "created_at":
                continue
            normalized[key] = _strip_created_at(item)
        return normalized
    if isinstance(value, list):
        return [_strip_created_at(item) for item in value]
    return value


def _extract_codes(violations: Sequence[object]) -> list[str]:
    codes: list[str] = []
    for item in violations:
        if isinstance(item, dict):
            code = item.get("code")
            if isinstance(code, str):
                codes.append(code)
    return codes


def test_mcp_tool_catalog_exposes_phase7_surface() -> None:
    adapter = MarsMCPAdapter()
    names = [item.name for item in adapter.list_tools()]

    assert names == [
        "mars.plan",
        "mars.simulate",
        "mars.governance",
        "mars.benchmark",
    ]


def test_mcp_planning_parity_matches_standalone_planner() -> None:
    goal = _goal()
    evidence = _evidence()

    standalone = CentralPlanner().plan(goal=goal, evidence=evidence)
    adapter = MarsMCPAdapter()
    result = adapter.invoke(
        "mars.plan",
        {
            "goal": _goal_payload(goal),
            "evidence": _evidence_payload(evidence),
        },
    )

    assert _strip_created_at(result["plan"]) == _strip_created_at(to_mcp_value(standalone))


def test_mcp_full_pipeline_parity_matches_standalone_flow() -> None:
    goal = _goal()
    evidence = _evidence()

    standalone_plan = CentralPlanner().plan(goal=goal, evidence=evidence)
    standalone_simulation = SimulationPipeline(seed=42, max_repair_attempts=2).run(standalone_plan)
    standalone_governance = GovernanceGate(min_confidence=0.75).evaluate(
        plan=standalone_plan,
        simulation=standalone_simulation,
    )
    standalone_benchmark = BenchmarkHarness().run(
        plan=standalone_plan,
        simulation=standalone_simulation,
    )

    adapter = MarsMCPAdapter()
    plan_result = adapter.invoke(
        "mars.plan",
        {
            "goal": _goal_payload(goal),
            "evidence": _evidence_payload(evidence),
        },
    )
    plan_id = plan_result["plan_id"]
    assert isinstance(plan_id, str)

    simulation_result = adapter.invoke(
        "mars.simulate",
        {
            "plan_id": plan_id,
            "seed": 42,
            "max_repair_attempts": 2,
        },
    )
    simulation_id = simulation_result["simulation_id"]
    assert isinstance(simulation_id, str)

    governance_result = adapter.invoke(
        "mars.governance",
        {
            "plan_id": plan_id,
            "simulation_id": simulation_id,
            "min_confidence": 0.75,
        },
    )
    benchmark_result = adapter.invoke(
        "mars.benchmark",
        {
            "plan_id": plan_id,
            "simulation_id": simulation_id,
        },
    )

    assert simulation_result["simulation"] == to_mcp_value(standalone_simulation)
    assert governance_result["governance"] == to_mcp_value(standalone_governance)
    assert benchmark_result["benchmark"] == to_mcp_value(standalone_benchmark)

    governance_payload = governance_result["governance"]
    assert isinstance(governance_payload, dict)
    raw_violations = governance_payload.get("violations")
    assert isinstance(raw_violations, list)
    assert _extract_codes(raw_violations) == [
        item.code for item in standalone_governance.violations
    ]