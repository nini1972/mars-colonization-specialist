from datetime import date

from mars_agent.knowledge.models import DocumentRecord, SearchQuery, TrustTier
from mars_agent.knowledge.retrieval import RetrievalIndex
from mars_agent.knowledge.source_registry import default_source_registry


def test_retrieval_filters_by_metadata_and_tier() -> None:
    registry = default_source_registry()
    docs = (
        DocumentRecord(
            doc_id="doc-1",
            source_id="nasa-std",
            title="ISRU oxygen",
            content="oxygen extraction from atmospheric CO2",
            published_on=date(2025, 1, 1),
            mission="perseverance",
            subsystem="isru",
        ),
        DocumentRecord(
            doc_id="doc-2",
            source_id="preprint",
            title="Experimental regolith idea",
            content="novel experimental concept",
            published_on=date(2025, 1, 2),
            mission="perseverance",
            subsystem="isru",
        ),
    )

    index = RetrievalIndex.from_documents(documents=docs, source_registry=registry)
    results = index.search(
        SearchQuery(
            text="oxygen extraction",
            top_k=5,
            min_tier=TrustTier.PEER_REVIEWED,
            subsystem="isru",
        )
    )

    assert len(results) == 1
    assert results[0].doc_id == "doc-1"
