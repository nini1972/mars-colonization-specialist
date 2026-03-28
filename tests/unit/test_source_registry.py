from mars_agent.knowledge.models import TrustTier
from mars_agent.knowledge.source_registry import default_source_registry


def test_source_registry_ranks_by_tier_then_review_date() -> None:
    registry = default_source_registry()
    ranked = registry.ranked()
    assert ranked[0].tier == TrustTier.AGENCY_STANDARD
    assert ranked[-1].tier == TrustTier.PREPRINT
