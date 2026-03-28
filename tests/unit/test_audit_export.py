import json
from datetime import date
from pathlib import Path

from mars_agent.governance import AuditTrailExporter, BenchmarkHarness, GovernanceGate
from mars_agent.knowledge.models import TrustTier
from mars_agent.orchestration import CentralPlanner, MissionGoal, MissionPhase
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.simulation import SimulationPipeline


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-phase6-audit",
            title="Phase 6 audit evidence",
            url="https://example.org/nasa-phase6-audit",
            published_on=date(2026, 3, 7),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.95,
        ),
    )


def _goal() -> MissionGoal:
    return MissionGoal(
        mission_id="mars-phase6-audit",
        crew_size=100,
        horizon_years=5.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=1200.0,
        battery_capacity_kwh=24000.0,
        dust_degradation_fraction=0.2,
        hours_without_sun=20.0,
    )


def test_audit_export_contains_governance_and_benchmark_sections(tmp_path: Path) -> None:
    plan = CentralPlanner().plan(goal=_goal(), evidence=_evidence())
    simulation = SimulationPipeline(seed=42, max_repair_attempts=2).run(plan)
    governance = GovernanceGate().evaluate(plan=plan, simulation=simulation)
    benchmark = BenchmarkHarness().run(plan=plan, simulation=simulation)

    exporter = AuditTrailExporter()
    record = exporter.build_record(
        plan=plan,
        simulation=simulation,
        governance=governance,
        benchmark=benchmark,
    )

    audit_path = tmp_path / "audit.json"
    exporter.export(record, audit_path)

    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    assert payload["mission_id"] == "mars-phase6-audit"
    assert "governance" in payload
    assert "benchmark" in payload
    assert "simulation" in payload
