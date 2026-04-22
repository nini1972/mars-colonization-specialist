"""Release hardening helpers for quarterly packs and hotfix bulletins."""

from __future__ import annotations

from dataclasses import dataclass, field

from mars_agent.governance.models import BenchmarkReport, GovernanceReport, ReleaseBulletin
from mars_agent.knowledge.release_workflow import ReleaseManifest, ReleasePlanner


@dataclass(slots=True)
class ReleaseHardeningManager:
    """Finalizes release artifacts only when governance and benchmarks pass."""

    release_planner: ReleasePlanner = field(default_factory=ReleasePlanner)
    governance_policy_source: str = "configs/knowledge_release_policy.toml"

    def _ensure_hardening(
        self,
        governance: GovernanceReport,
        benchmark: BenchmarkReport,
    ) -> None:
        if not governance.accepted:
            raise ValueError("Cannot finalize release: governance gate did not pass")
        if not benchmark.passed:
            raise ValueError("Cannot finalize release: benchmark harness did not pass")

    def finalize_quarterly(
        self,
        version: str,
        changed_doc_ids: tuple[str, ...],
        notes: str,
        governance: GovernanceReport,
        benchmark: BenchmarkReport,
    ) -> tuple[ReleaseManifest, ReleaseBulletin]:
        self._ensure_hardening(governance, benchmark)
        artifact_provenance = (
            self.governance_policy_source,
            benchmark.policy_source,
            "governance.audit_record.v1",
        )
        manifest = self.release_planner.create_quarterly_release(
            version=version,
            changed_doc_ids=changed_doc_ids,
            notes=notes,
            benchmark_profile=benchmark.profile,
            benchmark_policy_version=benchmark.policy_version,
            artifact_provenance=artifact_provenance,
        )
        bulletin = ReleaseBulletin(
            version=manifest.version,
            release_type=manifest.release_type,
            governance_passed=governance.accepted,
            benchmark_passed=benchmark.passed,
            governance_policy_version=manifest.governance_policy_version,
            benchmark_profile=manifest.benchmark_profile,
            benchmark_policy_version=manifest.benchmark_policy_version,
            changed_doc_ids=manifest.changed_doc_ids,
            rationale_type=manifest.rationale_type,
            summary=manifest.notes,
        )
        return manifest, bulletin

    def finalize_hotfix(
        self,
        version: str,
        changed_doc_ids: tuple[str, ...],
        reason: str,
        governance: GovernanceReport,
        benchmark: BenchmarkReport,
    ) -> tuple[ReleaseManifest, ReleaseBulletin]:
        self._ensure_hardening(governance, benchmark)
        artifact_provenance = (
            self.governance_policy_source,
            benchmark.policy_source,
            "governance.audit_record.v1",
        )
        manifest = self.release_planner.create_hotfix_release(
            version=version,
            changed_doc_ids=changed_doc_ids,
            reason=reason,
            benchmark_profile=benchmark.profile,
            benchmark_policy_version=benchmark.policy_version,
            artifact_provenance=artifact_provenance,
        )
        bulletin = ReleaseBulletin(
            version=manifest.version,
            release_type=manifest.release_type,
            governance_passed=governance.accepted,
            benchmark_passed=benchmark.passed,
            governance_policy_version=manifest.governance_policy_version,
            benchmark_profile=manifest.benchmark_profile,
            benchmark_policy_version=manifest.benchmark_policy_version,
            changed_doc_ids=manifest.changed_doc_ids,
            rationale_type=manifest.rationale_type,
            summary=manifest.notes,
        )
        return manifest, bulletin


def render_bulletin_markdown(bulletin: ReleaseBulletin) -> str:
    """Render release bulletin text for operators and review boards."""

    docs = ", ".join(bulletin.changed_doc_ids)
    return (
        f"# Release Bulletin {bulletin.version}\n\n"
        f"- Type: {bulletin.release_type}\n"
        f"- Governance passed: {bulletin.governance_passed}\n"
        f"- Benchmark passed: {bulletin.benchmark_passed}\n"
        f"- Governance policy: {bulletin.governance_policy_version}\n"
        f"- Benchmark policy: {bulletin.benchmark_profile}@{bulletin.benchmark_policy_version}\n"
        f"- Changed docs: {docs}\n\n"
        f"Rationale type: {bulletin.rationale_type}\n\n"
        f"Summary:\n{bulletin.summary}\n"
    )
