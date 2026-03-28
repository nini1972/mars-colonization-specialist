"""Core typed models for Phase 2 reasoning kernel."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum

from mars_agent.knowledge.models import TrustTier


class ViolationCode(StrEnum):
    """Canonical validation codes surfaced by the global gate."""

    CONSTRAINT_VIOLATION = "constraint_violation"
    PROVENANCE_MISSING = "provenance_missing"
    CONFIDENCE_TOO_LOW = "confidence_too_low"


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    """Represents a failed hard constraint and its observed values."""

    code: ViolationCode
    constraint_id: str
    message: str
    expected: float | None = None
    actual: float | None = None


@dataclass(frozen=True, slots=True)
class ForecastInput:
    """Uncertain model input used by probabilistic forecasters."""

    name: str
    mean: float
    std_dev: float
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True, slots=True)
class ForecastRequest:
    """Forecast request contract shared by all specialist modules."""

    metric: str
    inputs: tuple[ForecastInput, ...]
    confidence_level: float = 0.95


@dataclass(frozen=True, slots=True)
class ForecastResult:
    """Forecast output with confidence interval and model assumptions."""

    metric: str
    expected_value: float
    lower_bound: float
    upper_bound: float
    confidence_level: float
    model_name: str
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    """A provenance record linked to a specific decision."""

    source_id: str
    doc_id: str
    title: str
    url: str
    published_on: date
    tier: TrustTier
    relevance_score: float


@dataclass(frozen=True, slots=True)
class DecisionAlternative:
    """Alternative recommendation that was considered."""

    option_id: str
    summary: str


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """Provenance-first recommendation artifact."""

    decision_id: str
    subsystem: str
    recommendation: str
    rationale: str
    assumptions: tuple[str, ...]
    alternatives: tuple[DecisionAlternative, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()
    confidence: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass(frozen=True, slots=True)
class GatePolicy:
    """Policy thresholds enforced by the global validation gate."""

    min_confidence: float = 0.7
    min_evidence_count: int = 1


@dataclass(frozen=True, slots=True)
class GateResult:
    """Validation output returned by the global gate contract."""

    accepted: bool
    violations: tuple[ConstraintViolation, ...]
