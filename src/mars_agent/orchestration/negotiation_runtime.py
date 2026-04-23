"""Deterministic in-process router for bounded negotiation rounds."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import cast

from mars_agent.orchestration.models import CrossDomainConflict
from mars_agent.orchestration.negotiation_protocol import (
    NegotiationEnvelope,
    NegotiationMailbox,
    NegotiationMessageKind,
    NegotiationSession,
)
from mars_agent.orchestration.registry import SpecialistRegistry
from mars_agent.specialists import Subsystem
from mars_agent.specialists.contracts import (
    TradeoffCounterProposal,
    TradeoffProposal,
    TradeoffReview,
    TradeoffReviewDisposition,
)


def _canonical_payload(payload: dict[str, object]) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(json.dumps(payload, sort_keys=True, separators=(",", ":"))),
    )


def _proposal_sort_key(proposal: TradeoffProposal) -> tuple[str, str, float, str]:
    return (
        proposal.subsystem.value,
        proposal.knob_name,
        proposal.suggested_delta,
        proposal.rationale,
    )


def _review_sort_key(review: TradeoffReview) -> tuple[str, str, str, str, str]:
    return (
        review.reviewer_subsystem.value,
        review.proposal_subsystem.value,
        review.knob_name,
        review.disposition.value,
        review.rationale,
    )


def _counter_sort_key(counter: TradeoffCounterProposal) -> tuple[str, str, str, float, str]:
    return (
        counter.reviewer_subsystem.value,
        counter.proposal_subsystem.value,
        counter.knob_name,
        counter.suggested_delta,
        counter.rationale,
    )


def _proposal_wave_signature(
    proposals: tuple[TradeoffProposal, ...],
) -> tuple[tuple[str, str, float], ...]:
    return tuple(
        (proposal.subsystem.value, proposal.knob_name, proposal.suggested_delta)
        for proposal in proposals
    )


@dataclass(frozen=True, slots=True)
class NegotiationRoundArtifacts:
    """Normalized outputs produced by bounded routed negotiation."""

    proposals: tuple[TradeoffProposal, ...]
    reviews: tuple[TradeoffReview, ...]
    counter_proposals: tuple[TradeoffCounterProposal, ...]


@dataclass(slots=True)
class NegotiationRouter:
    """Deterministic mailbox router for one in-process negotiation session."""

    session: NegotiationSession
    _mailboxes: dict[str, NegotiationMailbox] = field(default_factory=dict, repr=False)
    _logical_sequence: int = field(default=0, repr=False)

    def send(
        self,
        *,
        sender: str,
        recipients: tuple[str, ...],
        kind: NegotiationMessageKind,
        payload: dict[str, object],
        record: bool = True,
    ) -> tuple[NegotiationEnvelope, ...]:
        unique_recipients = tuple(sorted(set(recipients)))
        canonical_payload = _canonical_payload(payload)
        if record:
            self.session.transcript.record(
                sender=sender,
                recipients=unique_recipients,
                kind=kind,
                payload=canonical_payload,
            )
        sequence = self._logical_sequence
        self._logical_sequence += 1
        envelopes = tuple(
            NegotiationEnvelope(
                session_id=self.session.session_id,
                round_id=self.session.round_id,
                sequence=sequence,
                sender=sender,
                recipient=recipient,
                kind=kind,
                payload=canonical_payload,
            )
            for recipient in unique_recipients
        )
        for envelope in envelopes:
            mailbox = self._mailboxes.setdefault(
                envelope.recipient,
                NegotiationMailbox(recipient=envelope.recipient),
            )
            mailbox.enqueue(envelope)
        return envelopes

    def has_pending(self) -> bool:
        return any(mailbox.pending for mailbox in self._mailboxes.values())

    def drain(self) -> tuple[NegotiationEnvelope, ...]:
        drained: list[NegotiationEnvelope] = []
        for recipient in sorted(self._mailboxes):
            drained.extend(self._mailboxes[recipient].drain())
        return tuple(
            sorted(
                drained,
                key=lambda envelope: (
                    envelope.sequence,
                    envelope.recipient,
                    envelope.sender,
                    envelope.kind.value,
                ),
            )
        )


@dataclass(slots=True)
class NegotiationRuntime:
    """Runs bounded routed negotiation inside the planner process."""

    registry: SpecialistRegistry
    session: NegotiationSession
    participants: tuple[str, ...]
    conflicts: tuple[CrossDomainConflict, ...]
    max_review_rounds: int = 3
    router: NegotiationRouter = field(init=False)

    def __post_init__(self) -> None:
        if self.max_review_rounds < 1:
            raise ValueError("max_review_rounds must be >= 1")
        self.router = NegotiationRouter(session=self.session)

    @property
    def conflict_ids(self) -> tuple[str, ...]:
        return tuple(sorted({conflict.conflict_id for conflict in self.conflicts}))

    @staticmethod
    def _participant_subsystem(name: str) -> Subsystem:
        return Subsystem(name)

    @staticmethod
    def _proposal_payload(proposal: TradeoffProposal) -> dict[str, object]:
        return {
            "subsystem": proposal.subsystem.value,
            "knob_name": proposal.knob_name,
            "suggested_delta": proposal.suggested_delta,
            "rationale": proposal.rationale,
            "conflict_ids": list(proposal.conflict_ids),
        }

    @staticmethod
    def _proposal_from_payload(payload: dict[str, object]) -> TradeoffProposal:
        subsystem = Subsystem(str(payload["subsystem"]))
        conflict_ids = payload.get("conflict_ids", ())
        if isinstance(conflict_ids, (list, tuple)):
            normalized_conflicts = tuple(str(conflict_id) for conflict_id in conflict_ids)
        else:
            normalized_conflicts = ()
        suggested_delta_raw = payload["suggested_delta"]
        if not isinstance(suggested_delta_raw, (int, float)):
            raise ValueError("proposal payload suggested_delta must be numeric")
        return TradeoffProposal(
            subsystem=subsystem,
            knob_name=str(payload["knob_name"]),
            suggested_delta=float(suggested_delta_raw),
            rationale=str(payload["rationale"]),
            conflict_ids=normalized_conflicts,
        )

    @staticmethod
    def _review_payload(review: TradeoffReview) -> dict[str, object]:
        return {
            "reviewer_subsystem": review.reviewer_subsystem.value,
            "proposal_subsystem": review.proposal_subsystem.value,
            "knob_name": review.knob_name,
            "disposition": review.disposition.value,
            "rationale": review.rationale,
            "suggested_delta": review.suggested_delta,
        }

    @staticmethod
    def _counter_payload(counter: TradeoffCounterProposal) -> dict[str, object]:
        return {
            "reviewer_subsystem": counter.reviewer_subsystem.value,
            "proposal_subsystem": counter.proposal_subsystem.value,
            "knob_name": counter.knob_name,
            "suggested_delta": counter.suggested_delta,
            "rationale": counter.rationale,
            "conflict_ids": list(counter.conflict_ids),
        }

    def _seed_proposal_requests(self) -> None:
        self.router.send(
            sender="planner",
            recipients=self.participants,
            kind=NegotiationMessageKind.PROPOSAL_REQUESTED,
            payload={
                "conflict_ids": list(self.conflict_ids),
                "knobs": [
                    "isru_reduction_fraction",
                    "crew_reduction",
                    "dust_degradation_adjustment",
                ],
            },
            record=False,
        )


    def _collect_proposals(self) -> tuple[TradeoffProposal, ...]:
        raw_proposals: list[TradeoffProposal] = []
        for envelope in self.router.drain():
            if envelope.kind is not NegotiationMessageKind.PROPOSAL_REQUESTED:
                continue
            subsystem = self._participant_subsystem(envelope.recipient)
            specialist = self.registry.get(subsystem)
            for out_env in specialist.handle_negotiation_envelope(
                envelope,
                self.participants,
                self.conflict_ids,
            ):
                if out_env.kind is NegotiationMessageKind.PROPOSAL_SUBMITTED:
                    proposal = self._proposal_from_payload(out_env.payload)
                    raw_proposals.append(proposal)
                self.router.send(
                    sender=out_env.sender,
                    recipients=(out_env.recipient,),
                    kind=out_env.kind,
                    payload=out_env.payload,
                )
        return tuple(sorted(raw_proposals, key=_proposal_sort_key))

    def _route_proposals(self, proposals: tuple[TradeoffProposal, ...]) -> None:
        for proposal in proposals:
            peer_recipients = tuple(
                participant
                for participant in self.participants
                if participant != proposal.subsystem.value
            )
            recipients = tuple(
                sorted({"planner", *peer_recipients})
            )
            self.router.send(
                sender=proposal.subsystem.value,
                recipients=recipients,
                kind=NegotiationMessageKind.PROPOSAL_SUBMITTED,
                payload=self._proposal_payload(proposal),
            )


    def _collect_reviews(self) -> tuple[TradeoffReview, ...]:
        raw_reviews: list[TradeoffReview] = []
        for envelope in self.router.drain():
            if envelope.kind is not NegotiationMessageKind.PROPOSAL_SUBMITTED:
                continue
            if envelope.recipient == "planner":
                continue
            specialist = self.registry.get(self._participant_subsystem(envelope.recipient))
            for out_env in specialist.handle_negotiation_envelope(
                envelope,
                self.participants,
                self.conflict_ids,
            ):
                if out_env.kind is NegotiationMessageKind.PROPOSAL_REVIEWED:
                    review = self._review_from_payload(out_env.payload)
                    raw_reviews.append(review)
                self.router.send(
                    sender=out_env.sender,
                    recipients=(out_env.recipient,),
                    kind=out_env.kind,
                    payload=out_env.payload,
                )
        return tuple(sorted(raw_reviews, key=_review_sort_key))

    def _route_reviews(self, reviews: tuple[TradeoffReview, ...]) -> None:
        for review in reviews:
            self.router.send(
                sender=review.reviewer_subsystem.value,
                recipients=("planner", review.proposal_subsystem.value),
                kind=NegotiationMessageKind.PROPOSAL_REVIEWED,
                payload=self._review_payload(review),
            )

    @staticmethod
    def _build_counter_proposals(
        proposals: tuple[TradeoffProposal, ...],
        reviews: tuple[TradeoffReview, ...],
    ) -> tuple[TradeoffCounterProposal, ...]:
        proposal_conflict_ids = {
            (proposal.subsystem, proposal.knob_name): proposal.conflict_ids
            for proposal in proposals
        }
        counter_proposals = [
            TradeoffCounterProposal(
                reviewer_subsystem=review.reviewer_subsystem,
                proposal_subsystem=review.proposal_subsystem,
                knob_name=review.knob_name,
                suggested_delta=review.suggested_delta,
                rationale=review.rationale,
                conflict_ids=proposal_conflict_ids.get(
                    (review.proposal_subsystem, review.knob_name),
                    (),
                ),
            )
            for review in reviews
            if review.disposition is TradeoffReviewDisposition.COUNTER
            and review.suggested_delta is not None
        ]
        return tuple(sorted(counter_proposals, key=_counter_sort_key))

    def _route_counter_proposals(
        self,
        counter_proposals: tuple[TradeoffCounterProposal, ...],
    ) -> None:
        for counter in counter_proposals:
            self.router.send(
                sender=counter.reviewer_subsystem.value,
                recipients=("planner", counter.proposal_subsystem.value),
                kind=NegotiationMessageKind.COUNTER_PROPOSAL_SUBMITTED,
                payload=self._counter_payload(counter),
            )

    @staticmethod
    def _merge_proposals(
        proposals: tuple[TradeoffProposal, ...],
        updates: tuple[TradeoffProposal, ...],
    ) -> tuple[TradeoffProposal, ...]:
        merged = {
            (proposal.subsystem, proposal.knob_name): proposal for proposal in proposals
        }
        merged.update({(proposal.subsystem, proposal.knob_name): proposal for proposal in updates})
        return tuple(sorted(merged.values(), key=_proposal_sort_key))

    @staticmethod
    def _counter_proposals_to_revised_proposals(
        counter_proposals: tuple[TradeoffCounterProposal, ...],
    ) -> tuple[TradeoffProposal, ...]:
        return tuple(
            sorted(
                (
                    TradeoffProposal(
                        subsystem=counter.proposal_subsystem,
                        knob_name=counter.knob_name,
                        suggested_delta=counter.suggested_delta,
                        rationale=(
                            f"Revised after {counter.reviewer_subsystem.name} counter: "
                            f"{counter.rationale}"
                        ),
                        conflict_ids=counter.conflict_ids,
                    )
                    for counter in counter_proposals
                ),
                key=_proposal_sort_key,
            )
        )

    def execute(self) -> NegotiationRoundArtifacts:
        self._seed_proposal_requests()
        proposals = self._collect_proposals()
        converged_proposals = proposals
        current_round_proposals = proposals
        all_reviews: list[TradeoffReview] = []
        all_counter_proposals: list[TradeoffCounterProposal] = []
        seen_waves = {_proposal_wave_signature(current_round_proposals)}

        for _ in range(self.max_review_rounds):
            if not current_round_proposals:
                break
            self._route_proposals(current_round_proposals)
            reviews = self._collect_reviews()
            all_reviews.extend(reviews)
            self._route_reviews(reviews)
            counter_proposals = self._build_counter_proposals(current_round_proposals, reviews)
            all_counter_proposals.extend(counter_proposals)
            if not counter_proposals:
                break
            self._route_counter_proposals(counter_proposals)

            revised_proposals = self._counter_proposals_to_revised_proposals(counter_proposals)
            converged_proposals = self._merge_proposals(converged_proposals, revised_proposals)
            wave_signature = _proposal_wave_signature(revised_proposals)
            if wave_signature in seen_waves:
                break
            seen_waves.add(wave_signature)
            current_round_proposals = revised_proposals

        if self.router.has_pending():
            self.router.drain()
        return NegotiationRoundArtifacts(
            proposals=converged_proposals,
            reviews=tuple(all_reviews),
            counter_proposals=tuple(all_counter_proposals),
        )