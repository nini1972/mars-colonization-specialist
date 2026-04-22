import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from mars_agent.governance import BenchmarkHarness, BenchmarkReference, GovernanceWorkflow
from mars_agent.knowledge.models import TrustTier
from mars_agent.orchestration import CentralPlanner, MissionGoal, MissionPhase
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.simulation import SimulationPipeline


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-phase6-workflow",
            title="Phase 6 workflow evidence",
            url="https://example.org/nasa-phase6-workflow",
            published_on=date(2026, 3, 8),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.96,
        ),
    )


def _goal() -> MissionGoal:
    return MissionGoal(
        mission_id="mars-phase6-workflow",
        crew_size=100,
        horizon_years=5.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=1200.0,
        battery_capacity_kwh=24000.0,
        dust_degradation_fraction=0.2,
        hours_without_sun=20.0,
    )


def _permissive_workflow() -> GovernanceWorkflow:
    return GovernanceWorkflow(
        benchmark_harness=BenchmarkHarness(
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
            )
        )
    )


def test_governance_workflow_finalizes_quarterly_and_exports_audit(
    tmp_path: Path,
) -> None:
    plan = CentralPlanner().plan(goal=_goal(), evidence=_evidence())
    simulation = SimulationPipeline(seed=42, max_repair_attempts=2).run(plan)
    workflow = _permissive_workflow()
    audit_path = tmp_path / "review" / "audit.json"

    result = workflow.finalize_quarterly(
        plan=plan,
        simulation=simulation,
        version="2026.Q2",
        changed_doc_ids=("doc-1", "doc-2"),
        notes="Quarterly governance workflow release.",
        audit_path=audit_path,
    )

    assert result.review.governance.accepted is True
    assert result.review.benchmark.passed is True
    assert result.manifest.release_type == "quarterly"
    assert "Release Bulletin 2026.Q2" in result.bulletin_markdown

    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    assert payload["mission_id"] == "mars-phase6-workflow"
    assert payload["governance"]["accepted"] is True
    assert "benchmark" in payload


def test_governance_workflow_exports_release_bundle(tmp_path: Path) -> None:
    plan = CentralPlanner().plan(goal=_goal(), evidence=_evidence())
    simulation = SimulationPipeline(seed=42, max_repair_attempts=2).run(plan)
    workflow = _permissive_workflow()
    result = workflow.finalize_hotfix(
        plan=plan,
        simulation=simulation,
        version="2026.HF-01",
        changed_doc_ids=("doc-9",),
        reason="Critical correction.",
    )

    bundle = workflow.export_release_bundle(result, tmp_path / "bundle")

    manifest_payload = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    audit_payload = json.loads(bundle.audit_path.read_text(encoding="utf-8"))
    bulletin_text = bundle.bulletin_path.read_text(encoding="utf-8")

    assert manifest_payload["version"] == "2026.HF-01"
    assert manifest_payload["release_type"] == "hotfix"
    assert audit_payload["mission_id"] == "mars-phase6-workflow"
    assert "Release Bulletin 2026.HF-01" in bulletin_text


def test_governance_workflow_exports_audit_even_when_release_is_blocked(
    tmp_path: Path,
) -> None:
    plan = CentralPlanner().plan(goal=_goal(), evidence=_evidence())
    simulation = SimulationPipeline(seed=42, max_repair_attempts=2).run(plan)
    original = plan.subsystem_responses[0]
    modified_plan = replace(
        plan,
        subsystem_responses=(
            replace(original, decision=replace(original.decision, evidence=())),
            *plan.subsystem_responses[1:],
        ),
    )
    workflow = GovernanceWorkflow()
    audit_path = tmp_path / "blocked" / "audit.json"

    with pytest.raises(ValueError, match="governance gate"):
        workflow.finalize_hotfix(
            plan=modified_plan,
            simulation=simulation,
            version="2026.HF-01",
            changed_doc_ids=("doc-9",),
            reason="Critical correction.",
            audit_path=audit_path,
        )

    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    assert payload["governance"]["accepted"] is False
    assert "governance.provenance.missing_evidence" in payload["governance"]["violations"]