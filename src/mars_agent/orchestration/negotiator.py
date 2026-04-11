"""Multi-agent negotiation module for resolving cross-domain coupling constraints."""

import json
import logging
import os
from dataclasses import dataclass

from pydantic import BaseModel, Field

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

from mars_agent.orchestration.models import CrossDomainConflict, MissionGoal

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NegotiationRound:
    """Record of one completed negotiation round."""

    round_num: int
    reduction_fraction: float
    accepted: bool
    rationale: str


class NegotiationResult(BaseModel):
    """Result of an LLM-driven multi-agent negotiation."""

    accepted: bool = Field(
        ..., description="Whether the agents agreed on a viable reduction strategy."
    )
    isru_reduction_fraction: float = Field(
        ...,
        description="The negotiated ISRU feedstock reduction fraction (0.0 to 0.8)",
        ge=0.0,
        le=0.8,
    )
    rationale: str = Field(..., description="The rationale for the negotiated changes.")


class MultiAgentNegotiator:
    """Uses LLMs to orchestrate a negotiation between ECLSS, ISRU, and Power specialists.

    The ``OpenAI`` client is created once at construction and reused for the lifetime
    of this object.  A new ``CentralPlanner`` (and therefore a new negotiator) is
    constructed per request so there is no cross-request state leakage.
    """

    def __init__(self) -> None:
        self.is_enabled = os.environ.get("MARS_LLM_ORCHESTRATOR_ENABLED", "false").lower() == "true"
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.client = OpenAI(api_key=self.api_key) if HAS_OPENAI and self.api_key else None

    def _build_messages(
        self,
        goal: MissionGoal,
        conflicts: tuple[CrossDomainConflict, ...],
        current_reduction: float,
        history: tuple[NegotiationRound, ...],
    ) -> list[dict[str, str]]:
        """Construct the system + user messages for the LLM call."""
        conflict_descriptions = [
            f"- {c.severity.name}: {' vs '.join(s.name for s in c.impacted_subsystems)} "
            f"({c.description})"
            for c in conflicts
        ]

        system_prompt = (
            "You are the Chief Engineer Orchestrator for the "
            "Mars Colonization Specialist mission planner.\n"
            "Your job is to negotiate inputs between ECLSS, ISRU, and Power "
            "specialists to resolve conflicts.\n"
            "The only knob you have right now to resolve Power deficits is "
            "reducing the ISRU generic regolith feedstock.\n"
            "Return a JSON object conforming strictly to this format:\n"
            '{"accepted": bool, "isru_reduction_fraction": float, "rationale": "string"}\n'
            "isru_reduction_fraction must be between 0.0 and 0.8."
        )

        history_section = ""
        if history:
            lines = ["\n\nPrior negotiation rounds:"]
            for r in history:
                status = "accepted" if r.accepted else "rejected"
                lines.append(
                    f"  Round {r.round_num + 1}: {r.reduction_fraction * 100:.1f}%"
                    f" reduction \u2192 {status}. {r.rationale}"
                )
            history_section = "\n".join(lines)

        user_prompt = (
            f"Mission Goal: {goal.crew_size} crew, Phase: {goal.current_phase.name}.\n"
            f"Current ISRU feedstock reduction is {current_reduction * 100:.1f}%.\n"
            "The CouplingChecker returned these conflicts:\n"
            + "\n".join(conflict_descriptions)
            + history_section
            + "\n\nPlease propose a new ISRU feedstock reduction fraction to "
            "resolve the power deficit safely."
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def negotiate(
        self,
        goal: MissionGoal,
        conflicts: tuple[CrossDomainConflict, ...],
        current_reduction: float,
        history: tuple[NegotiationRound, ...] = (),
    ) -> tuple[bool, float, str]:
        """
        Executes a multi-agent negotiation loop to resolve specific conflicts.
        Returns a tuple of (success, new_reduction_fraction, rationale).
        """
        if not self.is_enabled or not self.client:
            logger.debug("MultiAgentNegotiator is disabled or missing credentials. Skipping.")
            return False, current_reduction, "Negotiator disabled."

        logger.info("Triggering LLM multi-agent negotiation for %d conflicts.", len(conflicts))

        try:
            response = self.client.chat.completions.create(
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                messages=self._build_messages(goal, conflicts, current_reduction, history),
                response_format={"type": "json_object"},
                temperature=0.2,
            )

            raw_json = response.choices[0].message.content
            if not raw_json:
                return False, current_reduction, "Empty response from LLM."

            result_dict = json.loads(raw_json)
            result = NegotiationResult(**result_dict)

            return result.accepted, result.isru_reduction_fraction, result.rationale

        except Exception:
            logger.error("LLM negotiation failed.", exc_info=True)
            return False, current_reduction, "Negotiator error; see logs."
