from datetime import UTC, date, datetime
from pathlib import Path

from mars_agent.knowledge.ingestion import IngestionPipeline
from mars_agent.knowledge.models import DocumentRecord
from mars_agent.knowledge.source_registry import default_source_registry


def test_ingestion_deduplicates_and_persists(tmp_path: Path) -> None:
    pipeline = IngestionPipeline(source_registry=default_source_registry())

    doc_a = DocumentRecord(
        doc_id="doc-a",
        source_id="nasa-std",
        title="Oxygen loop",
        content="MOXIE oxygen production baseline.",
        published_on=date(2025, 4, 10),
        mission="perseverance",
        subsystem="isru",
    )
    doc_b = DocumentRecord(
        doc_id="doc-b",
        source_id="nasa-std",
        title="Oxygen loop duplicate",
        content="MOXIE oxygen production baseline.",
        published_on=date(2025, 5, 10),
        mission="perseverance",
        subsystem="isru",
    )

    result = pipeline.ingest_batch(
        version="2026.Q1",
        incoming_documents=[doc_a, doc_b],
        created_at=datetime(2026, 3, 28, tzinfo=UTC),
    )

    assert result.kept_count == 1
    assert result.skipped_duplicates == 1
    assert result.snapshot.version == "2026.Q1"

    snapshot_path = tmp_path / "snapshot.json"
    pipeline.persist_snapshot(result.snapshot, snapshot_path)
    loaded = pipeline.load_snapshot(snapshot_path)
    assert loaded.version == result.snapshot.version
    assert len(loaded.documents) == 1
