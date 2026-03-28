from mars_agent.knowledge.ontology import OntologyStore


def test_ontology_neighbors_filter_by_predicate() -> None:
    store = OntologyStore()
    store.add_relation("isru", "depends_on", "power", ("doc-1",))
    store.add_relation("isru", "depends_on", "thermal", ("doc-2",))
    store.add_relation("eclss", "depends_on", "power", ("doc-3",))

    depends_on = store.neighbors("isru", predicate="depends_on")
    assert len(depends_on) == 2
    assert depends_on[0].subject == "isru"
