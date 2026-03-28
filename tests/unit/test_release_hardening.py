import pytest

from mars_agent.governance import (
    BenchmarkDelta,
    BenchmarkReport,
    GovernanceReport,
    ReleaseHardeningManager,
    render_bulletin_markdown,
)


def test_release_hardening_finalizes_quarterly_release() -> None:
    manager = ReleaseHardeningManager()
    governance = GovernanceReport(accepted=True, violations=(), checked_items=8)
    benchmark = BenchmarkReport(
        passed=True,
        deltas=(
            BenchmarkDelta(
                metric="load_margin_kw",
                observed=18.0,
                target=15.0,
                delta=3.0,
                within_tolerance=True,
                source="NASA margin guidance",
            ),
        ),
    )

    manifest, bulletin = manager.finalize_quarterly(
        version="2026.Q2",
        changed_doc_ids=("doc-1", "doc-2"),
        notes="Quarterly governance-hardened release.",
        governance=governance,
        benchmark=benchmark,
    )

    assert manifest.release_type == "quarterly"
    text = render_bulletin_markdown(bulletin)
    assert "Release Bulletin 2026.Q2" in text


def test_release_hardening_blocks_failed_governance() -> None:
    manager = ReleaseHardeningManager()
    governance = GovernanceReport(
        accepted=False,
        violations=(),
        checked_items=3,
    )
    benchmark = BenchmarkReport(passed=True, deltas=())

    with pytest.raises(ValueError, match="governance gate"):
        manager.finalize_hotfix(
            version="2026.HF-01",
            changed_doc_ids=("doc-3",),
            reason="Critical telemetry correction.",
            governance=governance,
            benchmark=benchmark,
        )
