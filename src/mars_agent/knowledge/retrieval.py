"""Metadata-filtered retrieval index with trust-weighted ranking."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from math import sqrt

from mars_agent.knowledge.models import DocumentRecord, SearchQuery, SearchResult, TrustTier
from mars_agent.knowledge.source_registry import SourceRegistry

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text.lower())


def _cosine_similarity(a: Counter[str], b: Counter[str]) -> float:
    overlap = set(a.keys()) & set(b.keys())
    numerator = sum(a[token] * b[token] for token in overlap)
    norm_a = sqrt(sum(value * value for value in a.values()))
    norm_b = sqrt(sum(value * value for value in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return numerator / (norm_a * norm_b)


@dataclass(slots=True)
class RetrievalIndex:
    """In-memory retrieval index built from normalized documents."""

    _documents: tuple[DocumentRecord, ...]
    _vectors: dict[str, Counter[str]]
    _source_registry: SourceRegistry

    @classmethod
    def from_documents(
        cls,
        documents: tuple[DocumentRecord, ...],
        source_registry: SourceRegistry,
    ) -> RetrievalIndex:
        vectors: dict[str, Counter[str]] = {}
        for document in documents:
            vectors[document.doc_id] = Counter(_tokenize(f"{document.title} {document.content}"))
        return cls(_documents=documents, _vectors=vectors, _source_registry=source_registry)

    def search(self, query: SearchQuery) -> tuple[SearchResult, ...]:
        query_vector = Counter(_tokenize(query.text))
        scored_results: list[SearchResult] = []

        for document in self._documents:
            source = self._source_registry.get(document.source_id)
            if source.tier < query.min_tier:
                continue
            if query.mission is not None and document.mission != query.mission:
                continue
            if query.subsystem is not None and document.subsystem != query.subsystem:
                continue
            if query.published_after is not None and document.published_on < query.published_after:
                continue

            similarity = _cosine_similarity(query_vector, self._vectors[document.doc_id])
            tier_weight = 1.0 + (float(source.tier) - float(TrustTier.PREPRINT)) * 0.2
            score = similarity * tier_weight
            if score <= 0.0:
                continue

            scored_results.append(
                SearchResult(
                    doc_id=document.doc_id,
                    title=document.title,
                    source_id=document.source_id,
                    tier=source.tier,
                    score=score,
                    mission=document.mission,
                    subsystem=document.subsystem,
                )
            )

        ranked = sorted(scored_results, key=lambda item: item.score, reverse=True)
        return tuple(ranked[: query.top_k])
