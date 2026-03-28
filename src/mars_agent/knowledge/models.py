"""Typed data models for the Mars knowledge foundation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import IntEnum


class TrustTier(IntEnum):
    """Source authority ranking used for evidence prioritization."""

    PREPRINT = 1
    PEER_REVIEWED = 2
    AGENCY_STANDARD = 3


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """Metadata describing an evidence source and its trust level."""

    source_id: str
    title: str
    publisher: str
    tier: TrustTier
    url: str
    last_reviewed: date
    subsystems: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """Normalized document unit used by ingestion and retrieval."""

    doc_id: str
    source_id: str
    title: str
    content: str
    published_on: date
    mission: str
    subsystem: str
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OntologyEdge:
    """Directed relation between Mars system entities."""

    subject: str
    predicate: str
    object: str
    evidence_doc_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KnowledgeSnapshot:
    """Versioned immutable snapshot of ingested knowledge."""

    version: str
    created_at: datetime
    documents: tuple[DocumentRecord, ...]
    ontology_edges: tuple[OntologyEdge, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Query parameters for metadata-aware evidence retrieval."""

    text: str
    top_k: int = 5
    min_tier: TrustTier = TrustTier.PREPRINT
    mission: str | None = None
    subsystem: str | None = None
    published_after: date | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Scored retrieval result with source trust metadata."""

    doc_id: str
    title: str
    source_id: str
    tier: TrustTier
    score: float
    mission: str
    subsystem: str
