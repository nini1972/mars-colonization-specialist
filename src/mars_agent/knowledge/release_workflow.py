"""Quarterly and hotfix release planning for knowledge snapshot governance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    """Release metadata for knowledge package governance."""

    version: str
    release_type: str
    created_at: datetime
    changed_doc_ids: tuple[str, ...]
    notes: str
    rationale_type: str
    governance_policy_version: str
    benchmark_profile: str
    benchmark_policy_version: str
    artifact_provenance: tuple[str, ...]


@dataclass(slots=True)
class ReleasePlanner:
    """Release orchestration helper for quarterly and hotfix lanes."""

    governance_policy_version: str = "knowledge-release-policy.v1"
    default_benchmark_profile: str = "nasa-esa-mission-review"
    default_benchmark_policy_version: str = "2026.03"
    default_artifact_provenance: tuple[str, ...] = field(
        default=(
            "configs/knowledge_release_policy.toml",
            "NASA/ESA mission review benchmark bundle",
            "governance.audit_record.v1",
        )
    )

    def create_quarterly_release(
        self,
        version: str,
        changed_doc_ids: tuple[str, ...],
        notes: str,
        benchmark_profile: str | None = None,
        benchmark_policy_version: str | None = None,
        artifact_provenance: tuple[str, ...] | None = None,
    ) -> ReleaseManifest:
        manifest = ReleaseManifest(
            version=version,
            release_type="quarterly",
            created_at=datetime.now(tz=UTC),
            changed_doc_ids=changed_doc_ids,
            notes=notes,
            rationale_type="release_notes",
            governance_policy_version=self.governance_policy_version,
            benchmark_profile=benchmark_profile or self.default_benchmark_profile,
            benchmark_policy_version=(
                benchmark_policy_version or self.default_benchmark_policy_version
            ),
            artifact_provenance=artifact_provenance or self.default_artifact_provenance,
        )
        self.validate_manifest(manifest)
        return manifest

    def create_hotfix_release(
        self,
        version: str,
        changed_doc_ids: tuple[str, ...],
        reason: str,
        benchmark_profile: str | None = None,
        benchmark_policy_version: str | None = None,
        artifact_provenance: tuple[str, ...] | None = None,
    ) -> ReleaseManifest:
        manifest = ReleaseManifest(
            version=version,
            release_type="hotfix",
            created_at=datetime.now(tz=UTC),
            changed_doc_ids=changed_doc_ids,
            notes=reason,
            rationale_type="hotfix_reason",
            governance_policy_version=self.governance_policy_version,
            benchmark_profile=benchmark_profile or self.default_benchmark_profile,
            benchmark_policy_version=(
                benchmark_policy_version or self.default_benchmark_policy_version
            ),
            artifact_provenance=artifact_provenance or self.default_artifact_provenance,
        )
        self.validate_manifest(manifest)
        return manifest

    def validate_manifest(self, manifest: ReleaseManifest) -> None:
        if not manifest.version.strip():
            raise ValueError("version must be non-empty")
        if manifest.release_type not in {"quarterly", "hotfix"}:
            raise ValueError("release_type must be quarterly or hotfix")
        if len(manifest.changed_doc_ids) == 0:
            raise ValueError("changed_doc_ids must contain at least one document")
        if not manifest.notes.strip():
            raise ValueError("notes must be non-empty")
        if not manifest.rationale_type.strip():
            raise ValueError("rationale_type must be non-empty")
        if not manifest.governance_policy_version.strip():
            raise ValueError("governance_policy_version must be non-empty")
        if not manifest.benchmark_profile.strip():
            raise ValueError("benchmark_profile must be non-empty")
        if not manifest.benchmark_policy_version.strip():
            raise ValueError("benchmark_policy_version must be non-empty")
        if len(manifest.artifact_provenance) == 0:
            raise ValueError("artifact_provenance must contain at least one item")
        if any(not item.strip() for item in manifest.artifact_provenance):
            raise ValueError("artifact_provenance items must be non-empty")
