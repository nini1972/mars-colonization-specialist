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
    assert hotfix.release_type == "hotfix"


def test_release_planner_rejects_empty_changes() -> None:
    planner = ReleasePlanner()
    with pytest.raises(ValueError):
        planner.create_hotfix_release(version="x", changed_doc_ids=(), reason="reason")
