"""Deterministic in-process negotiation protocol primitives.

These types provide the first step toward a peer-to-peer, message-driven
negotiation kernel without changing the public planner or MCP contracts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from mars_agent.specialists.contracts import (
    Subsystem,
    TradeoffCounterProposal,
    TradeoffProposal,
    TradeoffReview,
    TradeoffReviewDisposition,
)


class NegotiationMessageKind(StrEnum):
    """Canonical message kinds emitted during a negotiation session."""

    SESSION_STARTED = "session_started"
    CONFLICT_DETECTED = "conflict_detected"
    PROPOSAL_REQUESTED = "proposal_requested"
    PROPOSAL_SUBMITTED = "proposal_submitted"
    PROPOSAL_REVIEWED = "proposal_reviewed"
    COUNTER_PROPOSAL_SUBMITTED = "counter_proposal_submitted"
    MEMORY_REPLAYED = "memory_replayed"
    FALLBACK_APPLIED = "fallback_applied"
    PROPOSAL_ACCEPTED = "proposal_accepted"
    SESSION_CLOSED = "session_closed"


def _canonical_payload(payload: dict[str, object]) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(json.dumps(payload, sort_keys=True, separators=(",", ":"))),
    )


def _envelope_sort_key(envelope: NegotiationEnvelope) -> tuple[int, str, str, str]:
    return (
        envelope.sequence,
        envelope.recipient,
        envelope.sender,
        envelope.kind.value,
    )


@dataclass(frozen=True, slots=True)
class NegotiationMessage:
    """One deterministic negotiation event within a session transcript."""

    session_id: str
    round_id: str
    sequence: int
    sender: str
    recipients: tuple[str, ...]
    kind: NegotiationMessageKind
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class NegotiationEnvelope:
    """One routed negotiation envelope addressed to a single participant."""

    session_id: str
    round_id: str
    sequence: int
    sender: str
    recipient: str
    kind: NegotiationMessageKind
    payload: dict[str, object]


def make_negotiation_envelopes(
    *,
    template: NegotiationEnvelope,
    sender: str,
    recipients: tuple[str, ...],
    kind: NegotiationMessageKind,
    payload: dict[str, object],
) -> tuple[NegotiationEnvelope, ...]:
    canonical_payload = _canonical_payload(payload)
    return tuple(
        NegotiationEnvelope(
            session_id=template.session_id,
            round_id=template.round_id,
            sequence=template.sequence,
            sender=sender,
            recipient=recipient,
            kind=kind,
            payload=canonical_payload,
        )
        for recipient in tuple(sorted(set(recipients)))
    )


def proposal_payload(proposal: TradeoffProposal) -> dict[str, object]:
    return {
        "subsystem": proposal.subsystem.value,
        "knob_name": proposal.knob_name,
        "suggested_delta": proposal.suggested_delta,
        "rationale": proposal.rationale,
        "conflict_ids": list(proposal.conflict_ids),
    }


def proposal_from_payload(payload: dict[str, object]) -> TradeoffProposal:
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


def review_payload(review: TradeoffReview) -> dict[str, object]:
    return {
        "reviewer_subsystem": review.reviewer_subsystem.value,
        "proposal_subsystem": review.proposal_subsystem.value,
        "knob_name": review.knob_name,
        "disposition": review.disposition.value,
        "rationale": review.rationale,
        "suggested_delta": review.suggested_delta,
    }


def review_from_payload(payload: dict[str, object]) -> TradeoffReview:
    suggested_delta_raw = payload.get("suggested_delta")
    if suggested_delta_raw is not None and not isinstance(suggested_delta_raw, (int, float)):
        raise ValueError("review payload suggested_delta must be numeric or null")
    return TradeoffReview(
        reviewer_subsystem=Subsystem(str(payload["reviewer_subsystem"])),
        proposal_subsystem=Subsystem(str(payload["proposal_subsystem"])),
        knob_name=str(payload["knob_name"]),
        disposition=TradeoffReviewDisposition(str(payload["disposition"])),
        rationale=str(payload["rationale"]),
        suggested_delta=(
            float(suggested_delta_raw) if suggested_delta_raw is not None else None
        ),
    )


def counter_payload(counter: TradeoffCounterProposal) -> dict[str, object]:
    return {
        "reviewer_subsystem": counter.reviewer_subsystem.value,
        "proposal_subsystem": counter.proposal_subsystem.value,
        "knob_name": counter.knob_name,
        "suggested_delta": counter.suggested_delta,
        "rationale": counter.rationale,
        "conflict_ids": list(counter.conflict_ids),
    }


@dataclass(slots=True)
class NegotiationMailbox:
    """Deterministic in-memory queue for one participant."""

    recipient: str
    pending: list[NegotiationEnvelope] = field(default_factory=list)

    def enqueue(self, envelope: NegotiationEnvelope) -> None:
        self.pending.append(envelope)
        self.pending.sort(key=_envelope_sort_key)

    def drain(self) -> tuple[NegotiationEnvelope, ...]:
        drained = tuple(self.pending)
        self.pending.clear()
        return drained


@dataclass(slots=True)
class NegotiationTranscript:
    """Append-only deterministic transcript for one negotiation session."""

    session_id: str
    round_id: str
    messages: list[NegotiationMessage] = field(default_factory=list)

    def record(
        self,
        *,
        sender: str,
        recipients: tuple[str, ...] = (),
        kind: NegotiationMessageKind,
        payload: dict[str, object] | None = None,
    ) -> NegotiationMessage:
        message = NegotiationMessage(
            session_id=self.session_id,
            round_id=self.round_id,
            sequence=len(self.messages),
            sender=sender,
            recipients=tuple(sorted(recipients)),
            kind=kind,
            payload=_canonical_payload(payload or {}),
        )
        self.messages.append(message)
        return message


@dataclass(frozen=True, slots=True)
class NegotiationDecision:
    """Normalized result produced by a deterministic negotiation session."""

    accepted: bool
    isru_reduction_fraction: float
    crew_reduction: int
    dust_degradation_adjustment: float
    rationale: str
    is_fallback: bool
    source: str


@dataclass(slots=True)
class NegotiationSession:
    """Session wrapper pairing deterministic IDs with a transcript."""

    session_id: str
    round_id: str
    transcript: NegotiationTranscript

    @classmethod
    def create(
        cls,
        *,
        mission_id: str,
        round_index: int,
        conflict_ids: tuple[str, ...],
        current_reduction: float,
    ) -> NegotiationSession:
        canonical = json.dumps(
            {
                "mission_id": mission_id,
                "round_index": round_index,
                "conflict_ids": sorted(conflict_ids),
                "current_reduction": round(current_reduction, 6),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        session_id = f"neg-{digest[:16]}"
        round_id = f"round-{round_index}"
        return cls(
            session_id=session_id,
            round_id=round_id,
            transcript=NegotiationTranscript(session_id=session_id, round_id=round_id),
        )