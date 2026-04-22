"""Composed Phase 6 workflow for governance review and release hardening."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from mars_agent.governance.audit import AuditTrailExporter
from mars_agent.governance.benchmark import BenchmarkHarness
from mars_agent.governance.gate import GovernanceGate
from mars_agent.governance.models import BenchmarkReport, GovernanceReport, ReleaseBulletin
from mars_agent.governance.release import ReleaseHardeningManager, render_bulletin_markdown
from mars_agent.knowledge.release_workflow import ReleaseManifest
from mars_agent.orchestration.models import PlanResult
from mars_agent.simulation.pipeline import SimulationReport


@dataclass(frozen=True, slots=True)
class GovernanceWorkflowReview:
    """Deterministic governance review bundle for one plan/simulation pair."""

    governance: GovernanceReport
    benchmark: BenchmarkReport
    audit_record: dict[str, object]


@dataclass(frozen=True, slots=True)
class GovernanceReleaseResult:
    """Finalized release artifacts plus the underlying review bundle."""

    review: GovernanceWorkflowReview
    manifest: ReleaseManifest
    bulletin: ReleaseBulletin
    bulletin_markdown: str
    audit_path: Path | None = None


@dataclass(frozen=True, slots=True)
class GovernanceReleaseBundlePaths:
    """Filesystem paths for one persisted governance release bundle."""

    manifest_path: Path
    bulletin_path: Path
    audit_path: Path


@dataclass(slots=True)
class GovernanceWorkflow:
    """Runs the full Phase 6 governance, audit, benchmark, and release path."""

    governance_gate: GovernanceGate = field(default_factory=GovernanceGate)
    benchmark_harness: BenchmarkHarness = field(default_factory=BenchmarkHarness)
    audit_exporter: AuditTrailExporter = field(default_factory=AuditTrailExporter)
    release_manager: ReleaseHardeningManager = field(default_factory=ReleaseHardeningManager)

    def review(
        self,
        *,
        plan: PlanResult,
        simulation: SimulationReport,
    ) -> GovernanceWorkflowReview:
        governance = self.governance_gate.evaluate(plan=plan, simulation=simulation)
        benchmark = self.benchmark_harness.run(plan=plan, simulation=simulation)
        audit_record = self.audit_exporter.build_record(
            plan=plan,
            simulation=simulation,
            governance=governance,
            benchmark=benchmark,
        )
        return GovernanceWorkflowReview(
            governance=governance,
            benchmark=benchmark,
            audit_record=audit_record,
        )

    def finalize_quarterly(
        self,
        *,
        plan: PlanResult,
        simulation: SimulationReport,
        version: str,
        changed_doc_ids: tuple[str, ...],
        notes: str,
        audit_path: Path | None = None,
    ) -> GovernanceReleaseResult:
        review = self.review(plan=plan, simulation=simulation)
        if audit_path is not None:
            self.audit_exporter.export(review.audit_record, audit_path)
        manifest, bulletin = self.release_manager.finalize_quarterly(
            version=version,
            changed_doc_ids=changed_doc_ids,
            notes=notes,
            governance=review.governance,
            benchmark=review.benchmark,
        )
        return GovernanceReleaseResult(
            review=review,
            manifest=manifest,
            bulletin=bulletin,
            bulletin_markdown=render_bulletin_markdown(bulletin),
            audit_path=audit_path,
        )

    def finalize_hotfix(
        self,
        *,
        plan: PlanResult,
        simulation: SimulationReport,
        version: str,
        changed_doc_ids: tuple[str, ...],
        reason: str,
        audit_path: Path | None = None,
    ) -> GovernanceReleaseResult:
        review = self.review(plan=plan, simulation=simulation)
        if audit_path is not None:
            self.audit_exporter.export(review.audit_record, audit_path)
        manifest, bulletin = self.release_manager.finalize_hotfix(
            version=version,
            changed_doc_ids=changed_doc_ids,
            reason=reason,
            governance=review.governance,
            benchmark=review.benchmark,
        )
        return GovernanceReleaseResult(
            review=review,
            manifest=manifest,
            bulletin=bulletin,
            bulletin_markdown=render_bulletin_markdown(bulletin),
            audit_path=audit_path,
        )

    def export_release_bundle(
        self,
        result: GovernanceReleaseResult,
        output_dir: Path,
    ) -> GovernanceReleaseBundlePaths:
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{result.manifest.version}-{result.manifest.release_type}"
        manifest_path = output_dir / f"{stem}-manifest.json"
        bulletin_path = output_dir / f"{stem}-bulletin.md"
        audit_path = output_dir / f"{stem}-audit.json"

        manifest_payload = {
            "version": result.manifest.version,
            "release_type": result.manifest.release_type,
            "created_at": result.manifest.created_at.isoformat(),
            "changed_doc_ids": list(result.manifest.changed_doc_ids),
            "notes": result.manifest.notes,
            "rationale_type": result.manifest.rationale_type,
            "governance_policy_version": result.manifest.governance_policy_version,
            "benchmark_profile": result.manifest.benchmark_profile,
            "benchmark_policy_version": result.manifest.benchmark_policy_version,
            "artifact_provenance": list(result.manifest.artifact_provenance),
        }
        manifest_path.write_text(
            json.dumps(manifest_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        bulletin_path.write_text(result.bulletin_markdown, encoding="utf-8")
        self.audit_exporter.export(result.review.audit_record, audit_path)

        return GovernanceReleaseBundlePaths(
            manifest_path=manifest_path,
            bulletin_path=bulletin_path,
            audit_path=audit_path,
        )