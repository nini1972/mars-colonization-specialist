"""Standardized I/O contracts for specialist subsystem modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mars_agent.reasoning.models import DecisionRecord, EvidenceReference, GateResult


@dataclass(frozen=True, slots=True)
class TradeoffKnob:
    """Describes one adjustable parameter that a specialist is willing to negotiate on."""

    name: str
    description: str
    min_value: float
    max_value: float
    preferred_delta: float
    unit: str

    def __post_init__(self) -> None:
        if self.min_value > self.max_value:
            raise ValueError(
                f"TradeoffKnob '{self.name}': min_value must be <= max_value "
                f"({self.min_value} <= {self.max_value})"
            )
        if self.preferred_delta < 0.0:
            raise ValueError(
                f"TradeoffKnob '{self.name}': preferred_delta must be non-negative "
                f"(got {self.preferred_delta})"
            )


@dataclass(frozen=True, slots=True)
class SpecialistCapability:
    """Describes what a specialist accepts, produces, and can trade off."""

    subsystem: Subsystem
    accepts_inputs: tuple[str, ...]
    produces_metrics: tuple[str, ...]
    tradeoff_knobs: tuple[TradeoffKnob, ...]


class TradeoffReviewDisposition(StrEnum):
    """Supported peer-review outcomes for specialist-authored proposals."""

    ACKNOWLEDGE = "acknowledge"
    COUNTER = "counter"


@dataclass(frozen=True, slots=True)
class TradeoffProposal:
    """A deterministic specialist-authored proposal for one tradeoff knob."""

    subsystem: Subsystem
    knob_name: str
    suggested_delta: float
    rationale: str
    conflict_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.suggested_delta < 0.0:
            raise ValueError(
                f"TradeoffProposal '{self.knob_name}': suggested_delta must be non-negative "
                f"(got {self.suggested_delta})"
            )


@dataclass(frozen=True, slots=True)
class TradeoffReview:
    """A deterministic specialist review of a peer-authored proposal."""

    reviewer_subsystem: Subsystem
    proposal_subsystem: Subsystem
    knob_name: str
    disposition: TradeoffReviewDisposition
    rationale: str
    suggested_delta: float | None = None

    def __post_init__(self) -> None:
        if self.suggested_delta is not None and self.suggested_delta < 0.0:
            raise ValueError(
                f"TradeoffReview '{self.knob_name}': suggested_delta must be non-negative "
                f"(got {self.suggested_delta})"
            )


class Subsystem(StrEnum):
    """Supported subsystem identifiers for specialist modules."""

    ECLSS = "eclss"
    ISRU = "isru"
    POWER = "power"
    HABITAT_THERMODYNAMICS = "habitat_thermodynamics"


@dataclass(frozen=True, slots=True)
class UncertaintyBounds:
    """A scalar value represented by mean and uncertainty bounds."""

    mean: float
    lower: float
    upper: float
    unit: str

    def __post_init__(self) -> None:
        if not (self.lower <= self.mean <= self.upper):
            raise ValueError("Uncertainty bounds must satisfy lower <= mean <= upper")


@dataclass(frozen=True, slots=True)
class ModuleInput:
    """Named module input with uncertainty bounds."""

    name: str
    value: UncertaintyBounds


@dataclass(frozen=True, slots=True)
class ModuleMetric:
    """Named module output metric with uncertainty bounds."""

    name: str
    value: UncertaintyBounds


@dataclass(frozen=True, slots=True)
class ModuleRequest:
    """Typed request contract shared by all specialist modules."""

    mission: str
    subsystem: Subsystem
    inputs: tuple[ModuleInput, ...]
    evidence: tuple[EvidenceReference, ...]
    desired_confidence: float = 0.9

    def get_input(self, name: str) -> ModuleInput:
        for item in self.inputs:
            if item.name == name:
                return item
        raise KeyError(f"Missing input: {name}")


@dataclass(frozen=True, slots=True)
class ModuleResponse:
    """Typed response contract shared by all specialist modules."""

    subsystem: Subsystem
    decision: DecisionRecord
    metrics: tuple[ModuleMetric, ...]
    gate: GateResult

    def get_metric(self, name: str) -> ModuleMetric:
        for metric in self.metrics:
            if metric.name == name:
                return metric
        raise KeyError(f"Missing metric: {name}")
