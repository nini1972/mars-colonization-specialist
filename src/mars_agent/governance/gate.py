"""Hard governance acceptance gate for planning and simulation outputs."""

from __future__ import annotations

from dataclasses import dataclass

from mars_agent.governance.models import GovernanceReport, GovernanceViolation
from mars_agent.orchestration.models import PlanResult
from mars_agent.simulation.pipeline import SimulationReport


@dataclass(slots=True)
class GovernanceGate:
    """Enforces no-output policy without provenance and safety compliance."""

    min_confidence: float = 0.75

    def evaluate(self, plan: PlanResult, simulation: SimulationReport) -> GovernanceReport:
        violations: list[GovernanceViolation] = []
        checked_items = 0

        for response in plan.subsystem_responses:
            checked_items += 1
            if not response.decision.evidence:
                violations.append(
                    GovernanceViolation(
                        code="governance.provenance.missing_evidence",
                        message=(
                            f"Subsystem {response.subsystem.value} decision "
                            "has no evidence references."
                        ),
                    )
                )
            if response.decision.confidence < self.min_confidence:
                violations.append(
                    GovernanceViolation(
                        code="governance.provenance.low_confidence",
                        message=(
                            f"Subsystem {response.subsystem.value} confidence is below threshold "
                            f"({response.decision.confidence:.3f} < {self.min_confidence:.3f})."
                        ),
                    )
                )
            if not response.gate.accepted:
                violations.append(
                    GovernanceViolation(
                        code="governance.safety.module_gate_failed",
                        message=(
                            f"Subsystem {response.subsystem.value} failed safety gate validation."
                        ),
                    )
                )

        for scenario in simulation.scenarios:
            checked_items += 1
            if not scenario.passed:
                violations.append(
                    GovernanceViolation(
                        code="governance.safety.scenario_failed",
                        message=f"Scenario {scenario.scenario_name} failed simulation validation.",
                    )
                )

        return GovernanceReport(
            accepted=not violations,
            violations=tuple(violations),
            checked_items=checked_items,
        )
