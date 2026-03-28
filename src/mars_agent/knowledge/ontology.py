"""Ontology storage for Mars systems entities and relationships."""

from __future__ import annotations

from dataclasses import dataclass, field

from mars_agent.knowledge.models import OntologyEdge


@dataclass(slots=True)
class OntologyStore:
    """Mutable ontology builder with query helpers."""

    _edges: list[OntologyEdge] = field(default_factory=list)

    def add_edge(self, edge: OntologyEdge) -> None:
        self._edges.append(edge)

    def add_relation(
        self,
        subject: str,
        predicate: str,
        object_name: str,
        evidence_doc_ids: tuple[str, ...] = (),
    ) -> None:
        self.add_edge(
            OntologyEdge(
                subject=subject,
                predicate=predicate,
                object=object_name,
                evidence_doc_ids=evidence_doc_ids,
            )
        )

    def edges(self) -> tuple[OntologyEdge, ...]:
        return tuple(self._edges)

    def neighbors(self, entity: str, predicate: str | None = None) -> tuple[OntologyEdge, ...]:
        results: list[OntologyEdge] = []
        for edge in self._edges:
            if edge.subject != entity:
                continue
            if predicate is not None and edge.predicate != predicate:
                continue
            results.append(edge)
        return tuple(results)
