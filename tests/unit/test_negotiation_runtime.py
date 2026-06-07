from __future__ import annotations

from typing import Any, cast

from mars_agent.orchestration import ConflictSeverity, CrossDomainConflict, MitigationOption
from mars_agent.orchestration.negotiation_protocol import (
    NegotiationEnvelope,
    NegotiationMessageKind,
    NegotiationSession,
)
from mars_agent.orchestration.negotiation_runtime import NegotiationRouter, NegotiationRuntime
from mars_agent.orchestration.registry import SpecialistRegistry
from mars_agent.specialists import Subsystem
from mars_agent.specialists.contracts import SpecialistCapability, TradeoffProposal, TradeoffReview


def _conflict() -> CrossDomainConflict:
    return CrossDomainConflict(
        conflict_id="coupling.power_balance.shortfall",
        description="Combined load exceeds available power.",
        severity=ConflictSeverity.HIGH,
        impacted_subsystems=(Subsystem.ISRU, Subsystem.POWER),
        mitigations=(MitigationOption(rank=1, action="reduce feedstock"),),
    )


def test_router_drains_mailboxes_in_deterministic_order() -> None:
    session = NegotiationSession.create(
        mission_id="runtime-router-test",
        round_index=0,
        conflict_ids=("shortfall",),
        current_reduction=0.0,
    )
    router = NegotiationRouter(session=session)

    router.send(
        sender="planner",
        recipients=("power", "isru"),
        kind=NegotiationMessageKind.PROPOSAL_REQUESTED,
        payload={"conflict_ids": ["shortfall"]},
        record=False,
    )
    drained = router.drain()

    assert [(envelope.recipient, envelope.sender) for envelope in drained] == [
        ("isru", "planner"),
        ("power", "planner"),
    ]


def test_runtime_converges_counter_proposals_into_revised_round() -> None:
    session = NegotiationSession.create(
        mission_id="runtime-exec-test",
        round_index=0,
        conflict_ids=("coupling.power_balance.shortfall",),
        current_reduction=0.0,
    )
    session.transcript.record(
        sender="planner",
        kind=NegotiationMessageKind.SESSION_STARTED,
        payload={"mission_id": "runtime-exec-test"},
    )
    session.transcript.record(
        sender="planner",
        recipients=("isru", "power"),
        kind=NegotiationMessageKind.CONFLICT_DETECTED,
        payload={"conflict_ids": ["coupling.power_balance.shortfall"]},
    )
    session.transcript.record(
        sender="planner",
        recipients=("isru", "power"),
        kind=NegotiationMessageKind.PROPOSAL_REQUESTED,
        payload={"knobs": ["isru_reduction_fraction", "dust_degradation_adjustment"]},
    )

    runtime = NegotiationRuntime(
        registry=SpecialistRegistry.default(),
        session=session,
        participants=("isru", "power"),
        conflicts=(_conflict(),),
    )

    artifacts = runtime.execute()

    assert [
        (proposal.subsystem.value, proposal.knob_name, proposal.suggested_delta)
        for proposal in artifacts.proposals
    ] == [
        ("isru", "isru_reduction_fraction", 0.1),
        ("power", "dust_degradation_adjustment", 0.02),
    ]
    assert [
        (
            review.reviewer_subsystem.value,
            review.proposal_subsystem.value,
            review.knob_name,
            review.disposition.value,
            review.suggested_delta,
        )
        for review in artifacts.reviews
    ] == [
        ("isru", "power", "dust_degradation_adjustment", "acknowledge", None),
        ("power", "isru", "isru_reduction_fraction", "counter", 0.1),
        ("power", "isru", "isru_reduction_fraction", "acknowledge", None),
    ]
    assert [
        (
            counter.reviewer_subsystem.value,
            counter.proposal_subsystem.value,
            counter.knob_name,
            counter.suggested_delta,
        )
        for counter in artifacts.counter_proposals
    ] == [("power", "isru", "isru_reduction_fraction", 0.1)]
    assert [message.kind for message in session.transcript.messages] == [
        NegotiationMessageKind.SESSION_STARTED,
        NegotiationMessageKind.CONFLICT_DETECTED,
        NegotiationMessageKind.PROPOSAL_REQUESTED,
        NegotiationMessageKind.PROPOSAL_SUBMITTED,
        NegotiationMessageKind.PROPOSAL_SUBMITTED,
        NegotiationMessageKind.PROPOSAL_REVIEWED,
        NegotiationMessageKind.PROPOSAL_REVIEWED,
        NegotiationMessageKind.COUNTER_PROPOSAL_SUBMITTED,
        NegotiationMessageKind.PROPOSAL_SUBMITTED,
        NegotiationMessageKind.PROPOSAL_REVIEWED,
    ]


class _StubReviewer:
    """Minimal stub specialist that emits two PROPOSAL_REVIEWED envelopes per
    PROPOSAL_SUBMITTED: one addressed to 'planner' and one addressed to the
    proposal subsystem, mirroring the real specialist pattern."""

    def __init__(self, subsystem: Subsystem) -> None:
        self._subsystem = subsystem

    def capabilities(self) -> SpecialistCapability:
        return SpecialistCapability(
            subsystem=self._subsystem,
            accepts_inputs=(),
            produces_metrics=(),
            tradeoff_knobs=(),
        )

    def propose_tradeoffs(self, conflict_ids: tuple[str, ...]) -> tuple[TradeoffProposal, ...]:
        return ()

    def review_peer_proposals(
        self,
        proposals: tuple[TradeoffProposal, ...],
        conflict_ids: tuple[str, ...],
    ) -> tuple[TradeoffReview, ...]:
        return ()

    def handle_negotiation_envelope(
        self,
        envelope: NegotiationEnvelope,
        participants: tuple[str, ...],
        conflict_ids: tuple[str, ...],
    ) -> tuple[NegotiationEnvelope, ...]:
        if envelope.kind is not NegotiationMessageKind.PROPOSAL_SUBMITTED:
            return ()
        payload: dict[str, object] = {
            "reviewer_subsystem": self._subsystem.value,
            "proposal_subsystem": "isru",
            "knob_name": "isru_reduction_fraction",
            "disposition": "acknowledge",
            "rationale": "stub-review",
            "suggested_delta": None,
        }
        return (
            NegotiationEnvelope(
                session_id=envelope.session_id,
                round_id=envelope.round_id,
                sequence=envelope.sequence,
                sender=self._subsystem.value,
                recipient="planner",
                kind=NegotiationMessageKind.PROPOSAL_REVIEWED,
                payload=payload,
            ),
            NegotiationEnvelope(
                session_id=envelope.session_id,
                round_id=envelope.round_id,
                sequence=envelope.sequence,
                sender=self._subsystem.value,
                recipient="isru",
                kind=NegotiationMessageKind.PROPOSAL_REVIEWED,
                payload=payload,
            ),
        )


def _proposal_submitted_payload() -> dict[str, object]:
    return {
        "subsystem": "isru",
        "knob_name": "isru_reduction_fraction",
        "suggested_delta": 0.1,
        "rationale": "reduce power draw",
        "conflict_ids": ["coupling.power_balance.shortfall"],
    }


def test_collect_reviews_only_aggregates_planner_addressed_envelopes() -> None:
    """_collect_reviews returns only PROPOSAL_REVIEWED envelopes addressed to
    'planner', not the copies addressed to non-planner participants."""
    session = NegotiationSession.create(
        mission_id="collect-reviews-filter-test",
        round_index=0,
        conflict_ids=("coupling.power_balance.shortfall",),
        current_reduction=0.0,
    )
    router = NegotiationRouter(session=session)
    router.send(
        sender="isru",
        recipients=("power",),
        kind=NegotiationMessageKind.PROPOSAL_SUBMITTED,
        payload=_proposal_submitted_payload(),
        record=False,
    )

    registry = SpecialistRegistry()
    registry.register(cast(Any, _StubReviewer(Subsystem.POWER)))

    runtime = NegotiationRuntime(
        registry=registry,
        session=session,
        participants=("isru", "power"),
        conflicts=(_conflict(),),
    )
    runtime.router = router

    reviews = runtime._collect_reviews()

    # The stub emits two envelopes per proposal: one to "planner" and one to "isru".
    # Only the "planner"-addressed envelope should be aggregated.
    assert len(reviews) == 1
    assert reviews[0].reviewer_subsystem == Subsystem.POWER
    assert reviews[0].proposal_subsystem == Subsystem.ISRU


def test_non_planner_reviews_routed_correctly_via_route_reviews() -> None:
    """Reviews for non-planner participants are delivered via _route_reviews,
    ensuring no multi-recipient review message is dropped."""
    session = NegotiationSession.create(
        mission_id="collect-reviews-routing-test",
        round_index=0,
        conflict_ids=("coupling.power_balance.shortfall",),
        current_reduction=0.0,
    )
    router = NegotiationRouter(session=session)
    router.send(
        sender="isru",
        recipients=("power",),
        kind=NegotiationMessageKind.PROPOSAL_SUBMITTED,
        payload=_proposal_submitted_payload(),
        record=False,
    )

    registry = SpecialistRegistry()
    registry.register(cast(Any, _StubReviewer(Subsystem.POWER)))

    runtime = NegotiationRuntime(
        registry=registry,
        session=session,
        participants=("isru", "power"),
        conflicts=(_conflict(),),
    )
    runtime.router = router

    reviews = runtime._collect_reviews()
    assert reviews, "at least one review must be collected before routing"

    runtime._route_reviews(reviews)

    routed = runtime.router.drain()

    isru_reviews = [
        e for e in routed
        if e.recipient == "isru" and e.kind is NegotiationMessageKind.PROPOSAL_REVIEWED
    ]
    assert isru_reviews, "review must be routed to the proposal subsystem (isru) via _route_reviews"

    planner_reviews = [
        e for e in routed
        if e.recipient == "planner" and e.kind is NegotiationMessageKind.PROPOSAL_REVIEWED
    ]
    assert planner_reviews, "review must be routed to planner via _route_reviews"