"""Quarterly and hotfix release planning for knowledge snapshot governance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    """Release metadata for knowledge package governance."""

    version: str
    release_type: str
    created_at: datetime
    changed_doc_ids: tuple[str, ...]
    notes: str


@dataclass(slots=True)
class ReleasePlanner:
    """Release orchestration helper for quarterly and hotfix lanes."""

    def create_quarterly_release(
        self,
        version: str,
        changed_doc_ids: tuple[str, ...],
        notes: str,
    ) -> ReleaseManifest:
        manifest = ReleaseManifest(
            version=version,
            release_type="quarterly",
            created_at=datetime.now(tz=UTC),
            changed_doc_ids=changed_doc_ids,
            notes=notes,
        )
        self.validate_manifest(manifest)
        return manifest

    def create_hotfix_release(
        self,
        version: str,
        changed_doc_ids: tuple[str, ...],
        reason: str,
    ) -> ReleaseManifest:
        manifest = ReleaseManifest(
            version=version,
            release_type="hotfix",
            created_at=datetime.now(tz=UTC),
            changed_doc_ids=changed_doc_ids,
            notes=reason,
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
