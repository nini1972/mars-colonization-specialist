import pytest

from mars_agent.knowledge.release_workflow import ReleasePlanner


def test_release_planner_creates_quarterly_and_hotfix_manifests() -> None:
    planner = ReleasePlanner()

    quarterly = planner.create_quarterly_release(
        version="2026.Q2",
        changed_doc_ids=("doc-1",),
        notes="Quarterly validated knowledge pack.",
    )
    hotfix = planner.create_hotfix_release(
        version="2026.Q2-hotfix-1",
        changed_doc_ids=("doc-9",),
        reason="Safety correction for oxygen yield assumptions.",
    )

    assert quarterly.release_type == "quarterly"
    assert quarterly.rationale_type == "release_notes"
    assert quarterly.governance_policy_version == "knowledge-release-policy.v1"
    assert quarterly.benchmark_profile == "nasa-esa-mission-review"
    assert hotfix.release_type == "hotfix"
    assert hotfix.rationale_type == "hotfix_reason"
    assert "configs/knowledge_release_policy.toml" in hotfix.artifact_provenance


def test_release_planner_rejects_empty_changes() -> None:
    planner = ReleasePlanner()
    with pytest.raises(ValueError):
        planner.create_hotfix_release(version="x", changed_doc_ids=(), reason="reason")
