"""Typed governance, benchmark, and release hardening models for Phase 6."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GovernanceViolation:
    """Represents one governance policy violation."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class GovernanceReport:
    """Aggregated governance gate decision."""

    accepted: bool
    violations: tuple[GovernanceViolation, ...]
    checked_items: int


@dataclass(frozen=True, slots=True)
class BenchmarkReference:
    """Reference benchmark target with tolerance and source provenance."""

    metric: str
    target: float
    tolerance: float
    source: str


@dataclass(frozen=True, slots=True)
class BenchmarkDelta:
    """Observed-vs-reference metric delta result."""

    metric: str
    observed: float
    target: float
    delta: float
    within_tolerance: bool
    source: str


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    """Benchmark harness result summary."""

    passed: bool
    deltas: tuple[BenchmarkDelta, ...]
    profile: str = "nasa-esa-mission-review"
    policy_version: str = "2026.03"
    policy_source: str = "NASA/ESA mission review benchmark bundle"


@dataclass(frozen=True, slots=True)
class ReleaseBulletin:
    """Finalized governance release bulletin payload."""

    version: str
    release_type: str
    governance_passed: bool
    benchmark_passed: bool
    governance_policy_version: str
    benchmark_profile: str
    benchmark_policy_version: str
    changed_doc_ids: tuple[str, ...]
    rationale_type: str
    summary: str
