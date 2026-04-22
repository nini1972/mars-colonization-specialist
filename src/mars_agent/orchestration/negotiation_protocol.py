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


class NegotiationMessageKind(StrEnum):
    """Canonical message kinds emitted during a negotiation session."""

    SESSION_STARTED = "session_started"
    CONFLICT_DETECTED = "conflict_detected"
    PROPOSAL_REQUESTED = "proposal_requested"
    PROPOSAL_SUBMITTED = "proposal_submitted"
    PROPOSAL_REVIEWED = "proposal_reviewed"
    MEMORY_REPLAYED = "memory_replayed"
    FALLBACK_APPLIED = "fallback_applied"
    PROPOSAL_ACCEPTED = "proposal_accepted"
    SESSION_CLOSED = "session_closed"


def _canonical_payload(payload: dict[str, object]) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads(json.dumps(payload, sort_keys=True, separators=(",", ":"))),
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