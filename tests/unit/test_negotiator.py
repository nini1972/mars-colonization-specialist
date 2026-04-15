"""Unit tests for MultiAgentNegotiator — history propagation and mocked LLM path."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from mars_agent.orchestration.models import (
    ConflictSeverity,
    CrossDomainConflict,
    KnowledgeContext,
    MissionGoal,
    MissionPhase,
    MitigationOption,
    RetrievedEvidence,
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


def _negotiator_with_mock_client(
    response_payload: dict[str, Any],
) -> tuple[MultiAgentNegotiator, MagicMock]:
    """Build an enabled negotiator whose client is a MagicMock."""
    negotiator = MultiAgentNegotiator.__new__(MultiAgentNegotiator)
    negotiator.is_enabled = True
    negotiator.is_async_enabled = False
    negotiator.api_key = "test-key"
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(response_payload)
    mock_client.chat.completions.create.return_value = mock_response
    negotiator.client = mock_client
    negotiator.async_client = None
    return negotiator, mock_client


def _negotiator_with_async_mock_client(
    response_payload: dict[str, Any],
) -> tuple[MultiAgentNegotiator, MagicMock]:
    negotiator = MultiAgentNegotiator.__new__(MultiAgentNegotiator)
    negotiator.is_enabled = True
    negotiator.is_async_enabled = True
    negotiator.api_key = "test-key"

    mock_sync_client = MagicMock()
    mock_async_client = MagicMock()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(response_payload)
    mock_async_client.chat.completions.create = AsyncMock(return_value=mock_response)

    negotiator.client = mock_sync_client
    negotiator.async_client = mock_async_client
    return negotiator, mock_async_client


def test_negotiate_disabled_returns_fallback() -> None:
    """When the negotiator is disabled it returns
    (False, unchanged_reduction, 'Negotiator disabled.')."""
    negotiator = MultiAgentNegotiator.__new__(MultiAgentNegotiator)
    negotiator.is_enabled = False
    negotiator.is_async_enabled = False
    negotiator.api_key = ""
    negotiator.client = None
    negotiator.async_client = None

    accepted, reduction, crew_reduction, dust_adj, rationale = negotiator.negotiate(
        goal=_goal(),
        conflicts=(_conflict(),),
        current_reduction=0.3,
    )

    assert accepted is False
    assert reduction == 0.3
    assert crew_reduction == 0
    assert dust_adj == 0.0
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


def test_negotiate_crew_reduction_propagated() -> None:
    """crew_reduction returned by the LLM is propagated through the 5-tuple."""
    payload = {
        "accepted": True,
        "isru_reduction_fraction": 0.3,
        "crew_reduction": 2,
        "dust_degradation_adjustment": 0.0,
        "rationale": "Stand down two crew members.",
    }
    negotiator, _ = _negotiator_with_mock_client(payload)

    accepted, isru, crew, dust, rationale = negotiator.negotiate(
        goal=_goal(),
        conflicts=(_conflict(),),
        current_reduction=0.1,
    )

    assert accepted is True
    assert isru == 0.3
    assert crew == 2
    assert dust == 0.0
    assert rationale == "Stand down two crew members."


def test_negotiate_dust_adjustment_propagated() -> None:
    """dust_degradation_adjustment returned by the LLM is propagated through the 5-tuple."""
    payload = {
        "accepted": True,
        "isru_reduction_fraction": 0.2,
        "crew_reduction": 0,
        "dust_degradation_adjustment": 0.05,
        "rationale": "Schedule panel cleaning.",
    }
    negotiator, _ = _negotiator_with_mock_client(payload)

    accepted, isru, crew, dust, rationale = negotiator.negotiate(
        goal=_goal(),
        conflicts=(_conflict(),),
        current_reduction=0.1,
    )

    assert accepted is True
    assert isru == 0.2
    assert crew == 0
    assert dust == 0.05
    assert rationale == "Schedule panel cleaning."


def test_negotiate_extra_fields_default_to_zero() -> None:
    """When LLM omits the new fields, Pydantic defaults to 0 / 0.0."""
    payload = {
        "accepted": True,
        "isru_reduction_fraction": 0.4,
        "rationale": "Reduce feedstock only.",
    }
    negotiator, _ = _negotiator_with_mock_client(payload)

    accepted, isru, crew, dust, _ = negotiator.negotiate(
        goal=_goal(),
        conflicts=(_conflict(),),
        current_reduction=0.0,
    )

    assert accepted is True
    assert isru == 0.4
    assert crew == 0
    assert dust == 0.0


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


def test_negotiate_includes_knowledge_context_in_system_prompt() -> None:
    payload = {"accepted": True, "isru_reduction_fraction": 0.35, "rationale": "Use evidence."}
    negotiator, mock_client = _negotiator_with_mock_client(payload)

    knowledge_context = KnowledgeContext(
        top_evidence=(),
        retrieval_hits=(
            RetrievedEvidence(
                doc_id="doc-123",
                title="Power headroom guidance",
                tier="AGENCY_STANDARD",
                score=0.88,
                subsystem="power",
            ),
        ),
        ontology_hints=("power:depends_on:battery",),
        trust_weight=1.2,
    )

    negotiator.negotiate(
        goal=_goal(),
        conflicts=(_conflict(),),
        current_reduction=0.1,
        knowledge_context=knowledge_context,
    )

    call_kwargs = mock_client.chat.completions.create.call_args
    system_message = call_kwargs.kwargs["messages"][0]["content"]
    assert "Knowledge context (retrieval hits)" in system_message
    assert "doc-123" in system_message
    assert "power:depends_on:battery" in system_message


def test_negotiate_async_mode_uses_async_client() -> None:
    payload = {
        "accepted": True,
        "isru_reduction_fraction": 0.22,
        "crew_reduction": 1,
        "dust_degradation_adjustment": 0.01,
        "rationale": "Async path.",
    }
    negotiator, mock_async_client = _negotiator_with_async_mock_client(payload)

    accepted, isru, crew, dust, rationale = negotiator.negotiate(
        goal=_goal(),
        conflicts=(_conflict(),),
        current_reduction=0.0,
    )

    assert accepted is True
    assert isru == 0.22
    assert crew == 1
    assert dust == 0.01
    assert rationale == "Async path."
    assert mock_async_client.chat.completions.create.await_count == 1


def test_negotiate_async_fallbacks_to_sync_on_async_error() -> None:
    payload = {
        "accepted": True,
        "isru_reduction_fraction": 0.31,
        "crew_reduction": 0,
        "dust_degradation_adjustment": 0.0,
        "rationale": "Sync fallback after async failure.",
    }
    negotiator, mock_sync_client = _negotiator_with_mock_client(payload)
    negotiator.is_async_enabled = True

    mock_async_client = MagicMock()
    mock_async_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))
    negotiator.async_client = mock_async_client

    accepted, isru, crew, dust, rationale = negotiator.negotiate(
        goal=_goal(),
        conflicts=(_conflict(),),
        current_reduction=0.0,
    )

    assert accepted is True
    assert isru == 0.31
    assert crew == 0
    assert dust == 0.0
    assert rationale == "Sync fallback after async failure."
    assert mock_sync_client.chat.completions.create.call_count == 1


def test_negotiate_async_direct_returns_disabled_when_not_enabled() -> None:
    negotiator = MultiAgentNegotiator.__new__(MultiAgentNegotiator)
    negotiator.is_enabled = False
    negotiator.is_async_enabled = True
    negotiator.api_key = ""
    negotiator.client = None
    negotiator.async_client = None

    accepted, reduction, crew_reduction, dust_adj, rationale = asyncio.run(
        negotiator.negotiate_async(
            goal=_goal(),
            conflicts=(_conflict(),),
            current_reduction=0.4,
        )
    )

    assert accepted is False
    assert reduction == 0.4
    assert crew_reduction == 0
    assert dust_adj == 0.0
    assert rationale == "Negotiator disabled."
