from __future__ import annotations

from mars_agent.orchestration import ConflictSeverity, CrossDomainConflict, MitigationOption
from mars_agent.orchestration.negotiation_protocol import (
    NegotiationMessageKind,
    NegotiationSession,
)
from mars_agent.orchestration.negotiation_runtime import NegotiationRouter, NegotiationRuntime
from mars_agent.orchestration.registry import SpecialistRegistry
from mars_agent.specialists import Subsystem


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
        )
        for review in artifacts.reviews
    ] == [
        ("isru", "power", "dust_degradation_adjustment", "acknowledge"),
        ("power", "isru", "isru_reduction_fraction", "counter"),
        ("power", "isru", "isru_reduction_fraction", "acknowledge"),
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