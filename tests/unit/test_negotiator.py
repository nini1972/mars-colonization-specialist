"""Unit tests for MultiAgentNegotiator — history propagation and mocked LLM path."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from mars_agent.orchestration.models import (
    ConflictSeverity,
    CrossDomainConflict,
    MissionGoal,
    MissionPhase,
    MitigationOption,
)
from mars_agent.orchestration.negotiator import MultiAgentNegotiator, NegotiationRound
from mars_agent.specialists.contracts import Subsystem


def _goal() -> MissionGoal:
    return MissionGoal(
        mission_id="test-mission",
        crew_size=10,
        horizon_years=2.0,
        current_phase=MissionPhase.EARLY_OPERATIONS,
        solar_generation_kw=200.0,
        battery_capacity_kwh=3000.0,
        dust_degradation_fraction=0.2,
        hours_without_sun=20.0,
        desired_confidence=0.9,
    )


def _conflict() -> CrossDomainConflict:
    return CrossDomainConflict(
        conflict_id="coupling.power_balance.power_shortfall",
        description="Combined ECLSS+ISRU load exceeds generation.",
        severity=ConflictSeverity.HIGH,
        impacted_subsystems=(Subsystem.ECLSS, Subsystem.ISRU, Subsystem.POWER),
        mitigations=(MitigationOption(rank=1, action="Reduce ISRU feedstock."),),
    )


def _negotiator_with_mock_client(response_payload: dict) -> tuple[MultiAgentNegotiator, MagicMock]:
    """Build an enabled negotiator whose client is a MagicMock."""
    negotiator = MultiAgentNegotiator.__new__(MultiAgentNegotiator)
    negotiator.is_enabled = True
    negotiator.api_key = "test-key"
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(response_payload)
    mock_client.chat.completions.create.return_value = mock_response
    negotiator.client = mock_client
    return negotiator, mock_client


def test_negotiate_disabled_returns_fallback() -> None:
    """When the negotiator is disabled it returns (False, unchanged_reduction, 'Negotiator disabled.')."""
    negotiator = MultiAgentNegotiator.__new__(MultiAgentNegotiator)
    negotiator.is_enabled = False
    negotiator.api_key = ""
    negotiator.client = None

    accepted, reduction, rationale = negotiator.negotiate(
        goal=_goal(),
        conflicts=(_conflict(),),
        current_reduction=0.3,
    )

    assert accepted is False
    assert reduction == 0.3
    assert rationale == "Negotiator disabled."


def test_negotiate_empty_history_omits_prior_rounds_section() -> None:
    """With no history the user message should not mention prior rounds."""
    payload = {"accepted": True, "isru_reduction_fraction": 0.4, "rationale": "OK."}
    negotiator, mock_client = _negotiator_with_mock_client(payload)

    negotiator.negotiate(
        goal=_goal(),
        conflicts=(_conflict(),),
        current_reduction=0.0,
        history=(),
    )

    call_kwargs = mock_client.chat.completions.create.call_args
    user_message = call_kwargs.kwargs["messages"][1]["content"]
    assert "Prior negotiation rounds" not in user_message


def test_negotiate_includes_history_in_user_message() -> None:
    """History rounds appear in the user-prompt sent to the LLM."""
    payload = {"accepted": True, "isru_reduction_fraction": 0.6, "rationale": "Better."}
    negotiator, mock_client = _negotiator_with_mock_client(payload)

    history = (
        NegotiationRound(round_num=0, reduction_fraction=0.3, accepted=False, rationale="Too low."),
    )

    negotiator.negotiate(
        goal=_goal(),
        conflicts=(_conflict(),),
        current_reduction=0.3,
        history=history,
    )

    call_kwargs = mock_client.chat.completions.create.call_args
    user_message = call_kwargs.kwargs["messages"][1]["content"]
    assert "Prior negotiation rounds" in user_message
    assert "Round 1" in user_message
    assert "30.0% reduction" in user_message
    assert "rejected" in user_message
    assert "Too low." in user_message
