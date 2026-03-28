"""Symbolic hard-constraint engine for physics and safety rules."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mars_agent.reasoning.models import ConstraintViolation, ViolationCode


@dataclass(frozen=True, slots=True)
class EqualityConstraint:
    """Checks that two values are approximately equal within tolerance."""

    constraint_id: str
    left_key: str
    right_key: str
    tolerance: float = 1e-6

    def evaluate(self, state: dict[str, float]) -> ConstraintViolation | None:
        left = state[self.left_key]
        right = state[self.right_key]
        delta = abs(left - right)
        if delta <= self.tolerance:
            return None
        return ConstraintViolation(
            code=ViolationCode.CONSTRAINT_VIOLATION,
            constraint_id=self.constraint_id,
            message=(
                f"Equality constraint failed: {self.left_key}={left:.6f}, "
                f"{self.right_key}={right:.6f}, tolerance={self.tolerance:.6f}"
            ),
            expected=right,
            actual=left,
        )


@dataclass(frozen=True, slots=True)
class UpperBoundConstraint:
    """Checks that value does not exceed a maximum safety limit."""

    constraint_id: str
    metric_key: str
    maximum: float

    def evaluate(self, state: dict[str, float]) -> ConstraintViolation | None:
        value = state[self.metric_key]
        if value <= self.maximum:
            return None
        return ConstraintViolation(
            code=ViolationCode.CONSTRAINT_VIOLATION,
            constraint_id=self.constraint_id,
            message=(
                f"Upper-bound constraint failed: {self.metric_key}={value:.6f} "
                f"exceeds max={self.maximum:.6f}"
            ),
            expected=self.maximum,
            actual=value,
        )


@dataclass(frozen=True, slots=True)
class LowerBoundConstraint:
    """Checks that value stays above a minimum operability limit."""

    constraint_id: str
    metric_key: str
    minimum: float

    def evaluate(self, state: dict[str, float]) -> ConstraintViolation | None:
        value = state[self.metric_key]
        if value >= self.minimum:
            return None
        return ConstraintViolation(
            code=ViolationCode.CONSTRAINT_VIOLATION,
            constraint_id=self.constraint_id,
            message=(
                f"Lower-bound constraint failed: {self.metric_key}={value:.6f} "
                f"below min={self.minimum:.6f}"
            ),
            expected=self.minimum,
            actual=value,
        )


type HardConstraint = EqualityConstraint | UpperBoundConstraint | LowerBoundConstraint


@dataclass(slots=True)
class SymbolicRulesEngine:
    """Evaluates hard constraints and returns explicit violations."""

    constraints: Sequence[HardConstraint]

    def validate(self, state: dict[str, float]) -> tuple[ConstraintViolation, ...]:
        violations: list[ConstraintViolation] = []
        for constraint in self.constraints:
            violation = constraint.evaluate(state)
            if violation is not None:
                violations.append(violation)
        return tuple(violations)
