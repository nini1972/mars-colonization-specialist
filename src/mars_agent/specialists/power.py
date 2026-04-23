"""Power specialist module (generation, storage, balancing, dust degradation)."""

from __future__ import annotations

from dataclasses import dataclass, field

from mars_agent.orchestration.negotiation_protocol import (
    NegotiationEnvelope,
    NegotiationMessageKind,
    make_negotiation_envelopes,
    proposal_from_payload,
    proposal_payload,
    review_payload,
)
from mars_agent.reasoning import (
    ForecastInput,
    ForecastRequest,
    GatePolicy,
    GaussianMonteCarloForecaster,
    GlobalValidationGate,
    LowerBoundConstraint,
    SymbolicRulesEngine,
    UpperBoundConstraint,
)
from mars_agent.reasoning.models import DecisionRecord
from mars_agent.specialists.contracts import (
    ModuleMetric,
    ModuleRequest,
    ModuleResponse,
    SpecialistCapability,
    Subsystem,
    TradeoffKnob,
    TradeoffProposal,
    TradeoffReview,
    TradeoffReviewDisposition,
    UncertaintyBounds,
)


@dataclass(slots=True)
class PowerSpecialist:
    """Evaluates power generation/storage recommendations under uncertainty."""

    forecaster: GaussianMonteCarloForecaster = field(
        default_factory=lambda: GaussianMonteCarloForecaster(seed=123)
    )

    def capabilities(self) -> SpecialistCapability:
        """Return this specialist's self-description for negotiation."""
        return SpecialistCapability(
            subsystem=Subsystem.POWER,
            accepts_inputs=(
                "solar_generation_kw",
                "battery_capacity_kwh",
                "critical_load_kw",
                "dust_degradation_fraction",
                "hours_without_sun",
            ),
            produces_metrics=(
                "effective_generation_kw",
                "load_margin_kw",
                "storage_cover_hours",
            ),
            tradeoff_knobs=(
                TradeoffKnob(
                    name="dust_degradation_adjustment",
                    description=(
                        "Reduce effective dust degradation fraction via panel cleaning schedule"
                    ),
                    min_value=0.0,
                    max_value=0.1,
                    preferred_delta=0.02,
                    unit="fraction",
                ),
            ),
        )

    def propose_tradeoffs(self, conflict_ids: tuple[str, ...]) -> tuple[TradeoffProposal, ...]:
        relevant = tuple(
            sorted(
                cid
                for cid in conflict_ids
                if cid.startswith("coupling.power") or cid == "coupling.isru_power_share.high"
            )
        )
        if not relevant:
            return ()
        return (
            TradeoffProposal(
                subsystem=Subsystem.POWER,
                knob_name="dust_degradation_adjustment",
                suggested_delta=0.02,
                rationale=(
                    "Power recommends a cleaning/mitigation action equivalent to a 0.02 "
                    "dust-degradation reduction to recover generation."
                ),
                conflict_ids=relevant,
            ),
        )

    def review_peer_proposals(
        self,
        proposals: tuple[TradeoffProposal, ...],
        conflict_ids: tuple[str, ...],
    ) -> tuple[TradeoffReview, ...]:
        if not any(
            cid.startswith("coupling.power") or cid == "coupling.isru_power_share.high"
            for cid in conflict_ids
        ):
            return ()
        reviews: list[TradeoffReview] = []
        for proposal in proposals:
            if proposal.subsystem is Subsystem.POWER:
                continue
            if (
                proposal.knob_name == "isru_reduction_fraction"
                and proposal.suggested_delta < 0.1
            ):
                reviews.append(
                    TradeoffReview(
                        reviewer_subsystem=Subsystem.POWER,
                        proposal_subsystem=proposal.subsystem,
                        knob_name=proposal.knob_name,
                        disposition=TradeoffReviewDisposition.COUNTER,
                        rationale=(
                            "Power counters that a deeper ISRU trim is needed to restore "
                            "generation margin under the current shortfall."
                        ),
                        suggested_delta=0.1,
                    )
                )
                continue
            reviews.append(
                TradeoffReview(
                    reviewer_subsystem=Subsystem.POWER,
                    proposal_subsystem=proposal.subsystem,
                    knob_name=proposal.knob_name,
                    disposition=TradeoffReviewDisposition.ACKNOWLEDGE,
                    rationale=(
                        "Power acknowledges the peer proposal as a credible contributor "
                        "to margin recovery under constrained generation."
                    ),
                )
            )
        return tuple(reviews)

    def handle_negotiation_envelope(
        self,
        envelope: NegotiationEnvelope,
        participants: tuple[str, ...],
        conflict_ids: tuple[str, ...],
    ) -> tuple[NegotiationEnvelope, ...]:
        if envelope.recipient != Subsystem.POWER.value:
            return ()
        if envelope.kind is NegotiationMessageKind.PROPOSAL_REQUESTED:
            recipients = tuple(
                sorted(
                    {
                        "planner",
                        *(participant for participant in participants if participant != Subsystem.POWER.value),
                    }
                )
            )
            outgoing: list[NegotiationEnvelope] = []
            for proposal in self.propose_tradeoffs(conflict_ids):
                outgoing.extend(
                    make_negotiation_envelopes(
                        template=envelope,
                        sender=proposal.subsystem.value,
                        recipients=recipients,
                        kind=NegotiationMessageKind.PROPOSAL_SUBMITTED,
                        payload=proposal_payload(proposal),
                    )
                )
            return tuple(outgoing)
        if envelope.kind is not NegotiationMessageKind.PROPOSAL_SUBMITTED:
            return ()
        if envelope.sender == Subsystem.POWER.value:
            return ()
        proposal = proposal_from_payload(envelope.payload)
        outgoing: list[NegotiationEnvelope] = []
        for review in self.review_peer_proposals((proposal,), conflict_ids):
            outgoing.extend(
                make_negotiation_envelopes(
                    template=envelope,
                    sender=review.reviewer_subsystem.value,
                    recipients=("planner", review.proposal_subsystem.value),
                    kind=NegotiationMessageKind.PROPOSAL_REVIEWED,
                    payload=review_payload(review),
                )
            )
        return tuple(outgoing)

    def analyze(self, request: ModuleRequest) -> ModuleResponse:
        if request.subsystem is not Subsystem.POWER:
            raise ValueError("PowerSpecialist only accepts POWER requests")

        solar_generation_kw = request.get_input("solar_generation_kw").value.mean
        battery_capacity_kwh = request.get_input("battery_capacity_kwh").value.mean
        critical_load_kw = request.get_input("critical_load_kw").value.mean
        dust_degradation_fraction = request.get_input("dust_degradation_fraction").value.mean
        hours_without_sun = request.get_input("hours_without_sun").value.mean

        forecast = self.forecaster.forecast(
            ForecastRequest(
                metric="effective_generation_kw",
                confidence_level=request.desired_confidence,
                inputs=(
                    ForecastInput(
                        name="solar_generation_kw",
                        mean=solar_generation_kw,
                        std_dev=max(0.2, solar_generation_kw * 0.1),
                        minimum=0.0,
                    ),
                    ForecastInput(
                        name="dust_loss_kw",
                        mean=-solar_generation_kw * dust_degradation_fraction,
                        std_dev=max(0.1, solar_generation_kw * 0.04),
                    ),
                ),
            )
        )

        effective_generation_kw = forecast.expected_value
        load_margin_kw = effective_generation_kw - critical_load_kw
        storage_cover_hours = battery_capacity_kwh / max(critical_load_kw, 1e-6)

        state = {
            "dust_degradation_fraction": dust_degradation_fraction,
            "load_margin_kw": load_margin_kw,
            "storage_cover_hours": storage_cover_hours,
            "hours_without_sun": hours_without_sun,
        }

        gate = GlobalValidationGate(
            rules_engine=SymbolicRulesEngine(
                constraints=(
                    UpperBoundConstraint(
                        constraint_id="power.dust_degradation.max",
                        metric_key="dust_degradation_fraction",
                        maximum=0.9,
                    ),
                    LowerBoundConstraint(
                        constraint_id="power.load_margin.min",
                        metric_key="load_margin_kw",
                        minimum=0.0,
                    ),
                    LowerBoundConstraint(
                        constraint_id="power.storage_cover.min",
                        metric_key="storage_cover_hours",
                        minimum=hours_without_sun,
                    ),
                )
            ),
            policy=GatePolicy(min_confidence=0.75, min_evidence_count=1),
        )

        decision = DecisionRecord(
            decision_id="power.phase3.baseline",
            subsystem=Subsystem.POWER.value,
            recommendation=(
                "Maintain positive load margin and battery coverage through dust events "
                "before increasing discretionary loads."
            ),
            rationale="Preserves critical habitat services under degraded solar conditions.",
            assumptions=(
                "Critical load estimate captures ECLSS and ISRU essentials",
                "Dust degradation remains within modeled bounds",
            ),
            evidence=request.evidence,
            confidence=forecast.confidence_level,
        )

        gate_result = gate.evaluate(decision=decision, state=state, forecast=forecast)

        return ModuleResponse(
            subsystem=Subsystem.POWER,
            decision=decision,
            metrics=(
                ModuleMetric(
                    name="effective_generation_kw",
                    value=UncertaintyBounds(
                        mean=forecast.expected_value,
                        lower=forecast.lower_bound,
                        upper=forecast.upper_bound,
                        unit="kW",
                    ),
                ),
                ModuleMetric(
                    name="load_margin_kw",
                    value=UncertaintyBounds(
                        mean=load_margin_kw,
                        lower=forecast.lower_bound - critical_load_kw,
                        upper=forecast.upper_bound - critical_load_kw,
                        unit="kW",
                    ),
                ),
                ModuleMetric(
                    name="storage_cover_hours",
                    value=UncertaintyBounds(
                        mean=storage_cover_hours,
                        lower=storage_cover_hours * 0.95,
                        upper=storage_cover_hours * 1.05,
                        unit="h",
                    ),
                ),
            ),
            gate=gate_result,
        )
