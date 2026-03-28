"""Trust-tiered source registry and ranking utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from mars_agent.knowledge.models import SourceRecord, TrustTier


@dataclass(frozen=True, slots=True)
class SourceRegistry:
    """In-memory registry of known evidence sources."""

    _sources: dict[str, SourceRecord]

    @classmethod
    def from_records(cls, records: list[SourceRecord]) -> SourceRegistry:
        sources: dict[str, SourceRecord] = {}
        for record in records:
            if record.source_id in sources:
                raise ValueError(f"Duplicate source_id detected: {record.source_id}")
            sources[record.source_id] = record
        return cls(_sources=sources)

    def get(self, source_id: str) -> SourceRecord:
        try:
            return self._sources[source_id]
        except KeyError as exc:
            raise KeyError(f"Unknown source_id: {source_id}") from exc

    def all_sources(self) -> tuple[SourceRecord, ...]:
        return tuple(self._sources.values())

    def ranked(self) -> tuple[SourceRecord, ...]:
        return tuple(
            sorted(
                self._sources.values(),
                key=lambda item: (item.tier, item.last_reviewed),
                reverse=True,
            )
        )

    def has_source(self, source_id: str) -> bool:
        return source_id in self._sources


def default_source_registry() -> SourceRegistry:
    """Return baseline source trust registry for Mars preparation."""
    return SourceRegistry.from_records(
        [
            SourceRecord(
                source_id="nasa-std",
                title="NASA Standards and Technical Notes",
                publisher="NASA",
                tier=TrustTier.AGENCY_STANDARD,
                url="https://www.nasa.gov",
                last_reviewed=date(2026, 1, 15),
                subsystems=("eclss", "isru", "power", "structures"),
            ),
            SourceRecord(
                source_id="esa-std",
                title="ESA Technical Publications",
                publisher="ESA",
                tier=TrustTier.AGENCY_STANDARD,
                url="https://www.esa.int",
                last_reviewed=date(2026, 1, 20),
                subsystems=("isru", "power", "thermal"),
            ),
            SourceRecord(
                source_id="peer-review",
                title="Peer-reviewed Journals",
                publisher="Various",
                tier=TrustTier.PEER_REVIEWED,
                url="https://doi.org",
                last_reviewed=date(2026, 2, 5),
                subsystems=("health", "biology", "materials"),
            ),
            SourceRecord(
                source_id="preprint",
                title="Preprint Archives",
                publisher="Various",
                tier=TrustTier.PREPRINT,
                url="https://arxiv.org",
                last_reviewed=date(2026, 2, 10),
                subsystems=("experimental",),
            ),
        ]
    )
