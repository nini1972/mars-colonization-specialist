"""Versioned ingestion pipeline with deduplication and provenance checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mars_agent.knowledge.models import DocumentRecord, KnowledgeSnapshot, OntologyEdge
from mars_agent.knowledge.source_registry import SourceRegistry


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Outcome of one ingestion batch."""

    snapshot: KnowledgeSnapshot
    kept_count: int
    skipped_duplicates: int


def _normalized_content(value: str) -> str:
    return " ".join(value.lower().split())


def _content_hash(record: DocumentRecord) -> str:
    payload = f"{record.source_id}|{_normalized_content(record.content)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class IngestionPipeline:
    """Ingest, deduplicate, validate and persist knowledge snapshots."""

    source_registry: SourceRegistry

    def ingest_batch(
        self,
        version: str,
        incoming_documents: list[DocumentRecord],
        previous_snapshot: KnowledgeSnapshot | None = None,
        ontology_edges: tuple[OntologyEdge, ...] = (),
        created_at: datetime | None = None,
    ) -> IngestionResult:
        for document in incoming_documents:
            if not self.source_registry.has_source(document.source_id):
                raise ValueError(
                    f"Unknown source for document {document.doc_id}: {document.source_id}"
                )

        existing_hashes: set[str] = set()
        existing_documents: list[DocumentRecord] = []
        if previous_snapshot is not None:
            existing_documents = list(previous_snapshot.documents)
            for record in previous_snapshot.documents:
                existing_hashes.add(_content_hash(record))

        kept_documents: list[DocumentRecord] = list(existing_documents)
        kept_count = 0
        skipped_duplicates = 0

        for document in incoming_documents:
            candidate_hash = _content_hash(document)
            if candidate_hash in existing_hashes:
                skipped_duplicates += 1
                continue

            existing_hashes.add(candidate_hash)
            kept_count += 1
            kept_documents.append(document)

        snapshot = KnowledgeSnapshot(
            version=version,
            created_at=created_at or datetime.now(tz=UTC),
            documents=tuple(kept_documents),
            ontology_edges=ontology_edges,
        )

        return IngestionResult(
            snapshot=snapshot,
            kept_count=kept_count,
            skipped_duplicates=skipped_duplicates,
        )

    def persist_snapshot(self, snapshot: KnowledgeSnapshot, path: Path) -> None:
        payload = {
            "version": snapshot.version,
            "created_at": snapshot.created_at.isoformat(),
            "documents": [
                {
                    "doc_id": doc.doc_id,
                    "source_id": doc.source_id,
                    "title": doc.title,
                    "content": doc.content,
                    "published_on": doc.published_on.isoformat(),
                    "mission": doc.mission,
                    "subsystem": doc.subsystem,
                    "metadata": dict(doc.metadata),
                }
                for doc in snapshot.documents
            ],
            "ontology_edges": [
                {
                    "subject": edge.subject,
                    "predicate": edge.predicate,
                    "object": edge.object,
                    "evidence_doc_ids": list(edge.evidence_doc_ids),
                }
                for edge in snapshot.ontology_edges
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load_snapshot(self, path: Path) -> KnowledgeSnapshot:
        payload = json.loads(path.read_text(encoding="utf-8"))

        documents = tuple(
            DocumentRecord(
                doc_id=item["doc_id"],
                source_id=item["source_id"],
                title=item["title"],
                content=item["content"],
                published_on=datetime.fromisoformat(item["published_on"]).date(),
                mission=item["mission"],
                subsystem=item["subsystem"],
                metadata=dict(item.get("metadata", {})),
            )
            for item in payload["documents"]
        )

        ontology_edges = tuple(
            OntologyEdge(
                subject=item["subject"],
                predicate=item["predicate"],
                object=item["object"],
                evidence_doc_ids=tuple(item.get("evidence_doc_ids", [])),
            )
            for item in payload["ontology_edges"]
        )

        return KnowledgeSnapshot(
            version=payload["version"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            documents=documents,
            ontology_edges=ontology_edges,
        )
