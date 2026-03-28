from datetime import date

from mars_agent.knowledge.models import TrustTier
from mars_agent.reasoning import (
    DecisionRecord,
    EvidenceReference,
    GatePolicy,
    GlobalValidationGate,
    LowerBoundConstraint,
    SymbolicRulesEngine,
    ViolationCode,
)


def _make_evidence() -> EvidenceReference:
    return EvidenceReference(
        source_id="nasa-std",
        doc_id="nasa-eclss-001",
        title="ECLSS operability baseline",
        url="https://example.org/nasa-eclss-001",
        published_on=date(2025, 10, 1),
        tier=TrustTier.AGENCY_STANDARD,
        relevance_score=0.95,
    )


def test_gate_rejects_invalid_recommendation_with_explicit_violations() -> None:
    rules = SymbolicRulesEngine(
        constraints=(
            LowerBoundConstraint(
                constraint_id="safety.o2_partial_pressure",
                metric_key="o2_partial_pressure_kpa",
                minimum=19.5,
            ),
        )
    )
    gate = GlobalValidationGate(rules_engine=rules, policy=GatePolicy(min_confidence=0.8))

    decision = DecisionRecord(
        decision_id="d-1",
        subsystem="eclss",
        recommendation="Reduce oxygen generation reserve to save power.",
        rationale="Assume crew activity remains low.",
        assumptions=("Crew activity remains low",),
        confidence=0.62,
        evidence=(),
    )

    result = gate.evaluate(
        decision=decision,
        state={"o2_partial_pressure_kpa": 17.0},
    )

    assert not result.accepted
    codes = {item.code for item in result.violations}
    assert ViolationCode.CONSTRAINT_VIOLATION in codes
    assert ViolationCode.PROVENANCE_MISSING in codes
    assert ViolationCode.CONFIDENCE_TOO_LOW in codes


def test_gate_accepts_recommendation_with_provenance_and_confidence() -> None:
    rules = SymbolicRulesEngine(
        constraints=(
            LowerBoundConstraint(
                constraint_id="safety.o2_partial_pressure",
                metric_key="o2_partial_pressure_kpa",
                minimum=19.5,
            ),
        )
    )
    gate = GlobalValidationGate(rules_engine=rules, policy=GatePolicy(min_confidence=0.75))

    decision = DecisionRecord(
        decision_id="d-2",
        subsystem="eclss",
        recommendation="Keep oxygen reserve at 30% to maintain safety margin.",
        rationale="Preserves pressure and recovery headroom.",
        assumptions=("Normal leak rates", "Crew size remains 12"),
        confidence=0.83,
        evidence=(_make_evidence(),),
    )

    result = gate.evaluate(
        decision=decision,
        state={"o2_partial_pressure_kpa": 20.8},
    )

    assert result.accepted
    assert result.violations == ()
