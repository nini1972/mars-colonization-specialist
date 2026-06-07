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
from mars_agent.specialists.contracts import (
    ModuleRequest,
    ModuleResponse,
    SpecialistCapability,
    TradeoffProposal,
    TradeoffReview,
)


def _conflict() -> CrossDomainConflict:
    return CrossDomainConflict(
        conflict_id="coupling.power_balance.shortfall",
        description="Combined load exceeds available power.",
        severity=ConflictSeverity.HIGH,
        impacted_subsystems=(Subsystem.ISRU, Subsystem.POWER),
        mitigations=(MitigationOption(rank=1, action="reduce feedstock"),),
    )


class _StubSpecialist:
    """Stub specialist that emits PROPOSAL_SUBMITTED to both planner and a peer recipient.

    Used to test that ``_collect_proposals`` filters the aggregated proposals to only
    planner-addressed envelopes while the non-planner copies are expected to reach peers
    via explicit routing (``_route_proposals`` / ``_route_reviews``).
    """

    def __init__(self, subsystem: Subsystem, peer_recipient: str) -> None:
        self._subsystem = subsystem
        self._peer_recipient = peer_recipient

    def capabilities(self) -> SpecialistCapability:
        return SpecialistCapability(
            subsystem=self._subsystem,
            accepts_inputs=(),
            produces_metrics=(),
            tradeoff_knobs=(),
        )

    def analyze(self, request: ModuleRequest) -> ModuleResponse:
        raise NotImplementedError

    def propose_tradeoffs(self, conflict_ids: tuple[str, ...]) -> tuple[TradeoffProposal, ...]:
        raise NotImplementedError

    def review_peer_proposals(
        self,
        proposals: tuple[TradeoffProposal, ...],
        conflict_ids: tuple[str, ...],
    ) -> tuple[TradeoffReview, ...]:
        raise NotImplementedError

    def handle_negotiation_envelope(
        self,
        envelope: NegotiationEnvelope,
        participants: tuple[str, ...],
        conflict_ids: tuple[str, ...],
    ) -> tuple[NegotiationEnvelope, ...]:
        if envelope.kind is not NegotiationMessageKind.PROPOSAL_REQUESTED:
            return ()
        payload: dict[str, object] = {
            "subsystem": self._subsystem.value,
            "knob_name": "stub_knob",
            "suggested_delta": 0.05,
            "rationale": "stub rationale",
            "conflict_ids": list(conflict_ids),
        }
        return (
            NegotiationEnvelope(
                session_id=envelope.session_id,
                round_id=envelope.round_id,
                sequence=envelope.sequence,
                sender=self._subsystem.value,
                recipient="planner",
                kind=NegotiationMessageKind.PROPOSAL_SUBMITTED,
                payload=payload,
            ),
            NegotiationEnvelope(
                session_id=envelope.session_id,
                round_id=envelope.round_id,
                sequence=envelope.sequence,
                sender=self._subsystem.value,
                recipient=self._peer_recipient,
                kind=NegotiationMessageKind.PROPOSAL_SUBMITTED,
                payload=payload,
            ),
        )


class _StubReviewer:
    """Minimal stub specialist that emits planner and non-planner review envelopes."""

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


def test_collect_proposals_only_aggregates_planner_addressed_envelopes() -> None:
    """Envelopes emitted to non-planner recipients are not counted as proposals.

    When a specialist responds to PROPOSAL_REQUESTED with envelopes addressed to
    both ``"planner"`` and a peer, ``_collect_proposals`` must include only the
    planner-addressed envelope in its returned tuple.  Peer-addressed copies must
    NOT be double-counted.
    """
    session = NegotiationSession.create(
        mission_id="collect-proposals-filter-test",
        round_index=0,
        conflict_ids=("shortfall",),
        current_reduction=0.0,
    )
    registry = SpecialistRegistry()
    stub = _StubSpecialist(subsystem=Subsystem.ISRU, peer_recipient="power")
    registry.register(stub)
    runtime = NegotiationRuntime(
        registry=registry,
        session=session,
        participants=("isru",),
        conflicts=(_conflict(),),
    )
    runtime._seed_proposal_requests()
    proposals = runtime._collect_proposals()

    # The stub emits PROPOSAL_SUBMITTED to both "planner" and "power".
    # Only the "planner"-addressed copy must be aggregated.
    assert len(proposals) == 1
    assert proposals[0].knob_name == "stub_knob"


def test_route_proposals_delivers_to_non_planner_participants() -> None:
    """Proposals reach non-planner participants via ``_route_proposals``.

    After ``_collect_proposals`` (which filters to planner-addressed envelopes only),
    ``_route_proposals`` must re-deliver the collected proposals to every participant
    so that specialist peers receive them for review.
    """
    session = NegotiationSession.create(
        mission_id="route-proposals-delivery-test",
        round_index=0,
        conflict_ids=("coupling.power_balance.shortfall",),
        current_reduction=0.0,
    )
    runtime = NegotiationRuntime(
        registry=SpecialistRegistry.default(),
        session=session,
        participants=("isru", "power"),
        conflicts=(_conflict(),),
    )
    runtime._seed_proposal_requests()
    proposals = runtime._collect_proposals()
    runtime._route_proposals(proposals)

    # Drain the router: every non-planner participant must have received at least one
    # PROPOSAL_SUBMITTED envelope, confirming they were not silently dropped.
    drained = runtime.router.drain()
    proposal_recipients = {
        env.recipient
        for env in drained
        if env.kind == NegotiationMessageKind.PROPOSAL_SUBMITTED
    }
    assert "isru" in proposal_recipients, "ISRU should receive proposals via _route_proposals"
    assert "power" in proposal_recipients, "POWER should receive proposals via _route_proposals"


def test_collect_reviews_only_aggregates_planner_addressed_envelopes() -> None:
    """Only PROPOSAL_REVIEWED envelopes addressed to ``"planner"`` enter the reviews tuple.

    Specialists emit review envelopes to both ``"planner"`` and the proposal author.
    ``_collect_reviews`` must include only the planner-addressed copies so that the
    returned tuple does not contain duplicates from peer-addressed copies.
    """
    session = NegotiationSession.create(
        mission_id="collect-reviews-filter-test",
        round_index=0,
        conflict_ids=("coupling.power_balance.shortfall",),
        current_reduction=0.0,
    )
    runtime = NegotiationRuntime(
        registry=SpecialistRegistry.default(),
        session=session,
        participants=("isru", "power"),
        conflicts=(_conflict(),),
    )
    runtime._seed_proposal_requests()
    proposals = runtime._collect_proposals()
    runtime._route_proposals(proposals)
    reviews = runtime._collect_reviews()

    # Each specialist reviews exactly one peer proposal, giving one review per pair.
    # Verify no duplicates by checking unique (reviewer, proposal) pairs.
    reviewer_proposal_pairs = {
        (review.reviewer_subsystem.value, review.proposal_subsystem.value)
        for review in reviews
    }
    assert ("isru", "power") in reviewer_proposal_pairs
    assert ("power", "isru") in reviewer_proposal_pairs
    # Total count must equal unique pairs — no peer-addressed duplicates.
    assert len(reviews) == len(reviewer_proposal_pairs)


def test_route_reviews_delivers_to_proposal_authors() -> None:
    """Proposal authors (non-planner) receive reviews via ``_route_reviews``.

    After ``_collect_reviews`` (which filters to planner-addressed envelopes),
    ``_route_reviews`` must re-deliver each review to the proposal's author subsystem
    so it can inform subsequent negotiation rounds.
    """
    session = NegotiationSession.create(
        mission_id="route-reviews-delivery-test",
        round_index=0,
        conflict_ids=("coupling.power_balance.shortfall",),
        current_reduction=0.0,
    )
    runtime = NegotiationRuntime(
        registry=SpecialistRegistry.default(),
        session=session,
        participants=("isru", "power"),
        conflicts=(_conflict(),),
    )
    runtime._seed_proposal_requests()
    proposals = runtime._collect_proposals()
    runtime._route_proposals(proposals)
    reviews = runtime._collect_reviews()
    runtime._route_reviews(reviews)

    # Drain the router: each proposal author must have received at least one
    # PROPOSAL_REVIEWED envelope, confirming reviews were not silently dropped.
    drained = runtime.router.drain()
    review_recipients = {
        env.recipient
        for env in drained
        if env.kind == NegotiationMessageKind.PROPOSAL_REVIEWED
    }
    assert "isru" in review_recipients, "ISRU (proposal author) should receive review via routing"
    assert "power" in review_recipients, "POWER (proposal author) should receive review via routing"


def test_collect_reviews_only_aggregates_planner_addressed_envelopes_stubbed_mailbox() -> None:
    """Stubbed review envelopes are aggregated only when addressed to planner."""
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

    assert len(reviews) == 1
    assert reviews[0].reviewer_subsystem == Subsystem.POWER
    assert reviews[0].proposal_subsystem == Subsystem.ISRU


def test_non_planner_reviews_routed_correctly_via_route_reviews_stubbed_mailbox() -> None:
    """Stubbed non-planner reviews are delivered by _route_reviews."""
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
        envelope
        for envelope in routed
        if envelope.recipient == "isru"
        and envelope.kind is NegotiationMessageKind.PROPOSAL_REVIEWED
    ]
    assert isru_reviews, "review must be routed to the proposal subsystem (isru) via _route_reviews"

    planner_reviews = [
        envelope
        for envelope in routed
        if envelope.recipient == "planner"
        and envelope.kind is NegotiationMessageKind.PROPOSAL_REVIEWED
    ]
    assert planner_reviews, "review must be routed to planner via _route_reviews"