"""Global physics/safety/provenance gate contract for module recommendations."""

from __future__ import annotations

from dataclasses import dataclass

from mars_agent.reasoning.models import (
    ConstraintViolation,
    DecisionRecord,
    ForecastResult,
    GatePolicy,
    GateResult,
    ViolationCode,
)
from mars_agent.reasoning.symbolic import SymbolicRulesEngine


@dataclass(slots=True)
class GlobalValidationGate:
    """Validates recommendation artifacts against shared acceptance policy."""

    rules_engine: SymbolicRulesEngine
    policy: GatePolicy = GatePolicy()

    def evaluate(
        self,
        decision: DecisionRecord,
        state: dict[str, float],
        forecast: ForecastResult | None = None,
    ) -> GateResult:
        violations: list[ConstraintViolation] = list(self.rules_engine.validate(state))

        if len(decision.evidence) < self.policy.min_evidence_count:
            violations.append(
                ConstraintViolation(
                    code=ViolationCode.PROVENANCE_MISSING,
                    constraint_id="provenance.evidence_count",
                    message=(
                        "Decision does not include enough evidence references "
                        f"({len(decision.evidence)} < {self.policy.min_evidence_count})"
                    ),
                    expected=float(self.policy.min_evidence_count),
                    actual=float(len(decision.evidence)),
                )
            )

        if decision.confidence < self.policy.min_confidence:
            violations.append(
                ConstraintViolation(
                    code=ViolationCode.CONFIDENCE_TOO_LOW,
                    constraint_id="provenance.confidence_threshold",
                    message=(
                        "Decision confidence is below the minimum threshold "
                        f"({decision.confidence:.3f} < {self.policy.min_confidence:.3f})"
                    ),
                    expected=self.policy.min_confidence,
                    actual=decision.confidence,
                )
            )

        if forecast is not None and forecast.confidence_level < self.policy.min_confidence:
            violations.append(
                ConstraintViolation(
                    code=ViolationCode.CONFIDENCE_TOO_LOW,
                    constraint_id="forecast.confidence_threshold",
                    message=(
                        "Forecast confidence level is below minimum policy "
                        f"({forecast.confidence_level:.3f} < {self.policy.min_confidence:.3f})"
                    ),
                    expected=self.policy.min_confidence,
                    actual=forecast.confidence_level,
                )
            )

        return GateResult(accepted=not violations, violations=tuple(violations))
