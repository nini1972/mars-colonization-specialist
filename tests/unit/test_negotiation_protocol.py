from __future__ import annotations

from datetime import date

from mars_agent.knowledge.models import TrustTier
from mars_agent.orchestration import (
    CentralPlanner,
    ConflictSeverity,
    CrossDomainConflict,
    KnowledgeContext,
    MissionGoal,
    MissionPhase,
    MitigationOption,
)
from mars_agent.orchestration.negotiation_protocol import (
    NegotiationMessageKind,
    NegotiationSession,
)
from mars_agent.orchestration.negotiation_store import (
    NegotiationMemoryStore,
    NegotiationOutcome,
    _conflict_fingerprint,
)
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.specialists import Subsystem


def _goal() -> MissionGoal:
    return MissionGoal(
        mission_id="negotiation-protocol-test",
        crew_size=80,
        horizon_years=2.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=80.0,
        battery_capacity_kwh=1000.0,
        dust_degradation_fraction=0.4,
        hours_without_sun=28.0,
        desired_confidence=0.9,
    )


def _evidence() -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            source_id="nasa-std",
            doc_id="nasa-neg-001",
            title="Negotiation protocol evidence",
            url="https://example.org/nasa-neg-001",
            published_on=date(2026, 4, 20),
            tier=TrustTier.AGENCY_STANDARD,
            relevance_score=0.95,
        ),
    )


def _knowledge_context(weight: float = 1.0) -> KnowledgeContext:
    return KnowledgeContext(top_evidence=_evidence(), trust_weight=weight)


def _conflict() -> CrossDomainConflict:
    return CrossDomainConflict(
        conflict_id="coupling.power_balance.shortfall",
        description="Combined load exceeds available power.",
        severity=ConflictSeverity.HIGH,
        impacted_subsystems=(Subsystem.ISRU, Subsystem.POWER),
        mitigations=(MitigationOption(rank=1, action="reduce feedstock"),),
    )


def test_negotiation_session_is_deterministic() -> None:
    left = NegotiationSession.create(
        mission_id="mission-a",
        round_index=2,
        conflict_ids=("b", "a"),
        current_reduction=0.25,
    )
    right = NegotiationSession.create(
        mission_id="mission-a",
        round_index=2,
        conflict_ids=("a", "b"),
        current_reduction=0.25,
    )

    assert left.session_id == right.session_id
    assert left.round_id == right.round_id == "round-2"


def test_negotiation_transcript_canonicalizes_message_shape() -> None:
    session = NegotiationSession.create(
        mission_id="mission-b",
        round_index=0,
        conflict_ids=("power",),
        current_reduction=0.1,
    )

    message = session.transcript.record(
        sender="planner",
        recipients=("power", "isru"),
        kind=NegotiationMessageKind.CONFLICT_DETECTED,
        payload={"z": 2, "a": 1},
    )

    assert message.sequence == 0
    assert message.recipients == ("isru", "power")
    assert list(message.payload) == ["a", "z"]


def test_run_negotiation_session_records_fallback_transcript() -> None:
    planner = CentralPlanner()

    decision, session = planner._run_negotiation_session(
        goal=_goal(),
        conflicts=(_conflict(),),
        current_reduction=0.0,
        knowledge_context=_knowledge_context(),
        history=(),
    )

    assert decision.is_fallback is True
    assert decision.accepted is False
    assert [message.kind for message in session.transcript.messages] == [
        NegotiationMessageKind.SESSION_STARTED,
        NegotiationMessageKind.CONFLICT_DETECTED,
        NegotiationMessageKind.PROPOSAL_REQUESTED,
        NegotiationMessageKind.FALLBACK_APPLIED,
        NegotiationMessageKind.PROPOSAL_ACCEPTED,
        NegotiationMessageKind.SESSION_CLOSED,
    ]


def test_run_negotiation_session_records_memory_replay_transcript() -> None:
    goal = _goal()
    conflicts = (_conflict(),)
    context = _knowledge_context()
    fingerprint = _conflict_fingerprint(goal, conflicts, context)
    store = NegotiationMemoryStore()
    store.store(
        NegotiationOutcome(
            conflict_fingerprint=fingerprint,
            isru_reduction_fraction=0.15,
            crew_reduction=0,
            dust_degradation_adjustment=0.0,
            rationale="seeded replay",
            accepted=True,
            is_fallback=True,
            stored_at="2026-04-20T00:00:00+00:00",
            hit_count=0,
        )
    )
    planner = CentralPlanner(negotiation_store=store)

    decision, session = planner._run_negotiation_session(
        goal=goal,
        conflicts=conflicts,
        current_reduction=0.0,
        knowledge_context=context,
        history=(),
    )

    assert decision.source == "memory"
    assert decision.rationale.startswith("[memory hit][fallback]")
    assert [message.kind for message in session.transcript.messages] == [
        NegotiationMessageKind.SESSION_STARTED,
        NegotiationMessageKind.CONFLICT_DETECTED,
        NegotiationMessageKind.PROPOSAL_REQUESTED,
        NegotiationMessageKind.MEMORY_REPLAYED,
        NegotiationMessageKind.PROPOSAL_ACCEPTED,
        NegotiationMessageKind.SESSION_CLOSED,
    ]