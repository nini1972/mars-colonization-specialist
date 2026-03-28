"""Standardized I/O contracts for specialist subsystem modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mars_agent.reasoning.models import DecisionRecord, EvidenceReference, GateResult


class Subsystem(StrEnum):
    """Supported subsystem identifiers for specialist modules."""

    ECLSS = "eclss"
    ISRU = "isru"
    POWER = "power"


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
