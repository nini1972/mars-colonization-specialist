"""Multi-agent negotiation module for resolving cross-domain coupling constraints."""

import json
import logging
import os
from dataclasses import dataclass

from pydantic import BaseModel, Field

try:
    from openai import OpenAI
    from openai.types.chat import ChatCompletionMessageParam
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    ChatCompletionMessageParam = object  # type: ignore[misc]

from mars_agent.orchestration.models import (
    CrossDomainConflict,
    KnowledgeContext,
    MissionGoal,
)
from mars_agent.specialists.contracts import SpecialistCapability

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NegotiationRound:
    """Record of one completed negotiation round."""

    round_num: int
    reduction_fraction: float
    accepted: bool
    rationale: str
    crew_reduction: int = 0
    dust_degradation_adjustment: float = 0.0


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
    crew_reduction: int = Field(
        default=0,
        description="Number of crew members to stand down from active duty (0–5).",
        ge=0,
        le=5,
    )
    dust_degradation_adjustment: float = Field(
        default=0.0,
        description="Reduction to apply to the dust degradation fraction (0.0–0.1).",
        ge=0.0,
        le=0.1,
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

    def _append_capability_preferences(
        self,
        system_prompt: str,
        capabilities: tuple[SpecialistCapability, ...],
    ) -> str:
        if not capabilities:
            return system_prompt

        supported_knobs = {
            "isru_reduction_fraction",
            "crew_reduction",
            "dust_degradation_adjustment",
        }
        lines = ["\n\nSpecialist preferences (preferred step sizes per knob):"]
        for cap in capabilities:
            for knob in cap.tradeoff_knobs:
                if knob.name not in supported_knobs or knob.preferred_delta <= 0.0:
                    continue
                lines.append(
                    f"  [{cap.subsystem.upper()}] {knob.name}: "
                    f"preferred_delta={knob.preferred_delta} {knob.unit}, "
                    f"range [{knob.min_value}, {knob.max_value}] - {knob.description}"
                )

        return system_prompt + "\n".join(lines) if len(lines) > 1 else system_prompt

    def _append_knowledge_context(
        self,
        system_prompt: str,
        knowledge_context: KnowledgeContext | None,
    ) -> str:
        if knowledge_context is None:
            return system_prompt

        sections = self._knowledge_sections(knowledge_context)

        if not sections:
            return system_prompt

        return (
            system_prompt
            + "\n\n"
            + "\n\n".join(sections)
            + "\n\nApply these knowledge signals conservatively and prioritize "
            "higher trust tiers when trade-offs conflict."
        )

    def _knowledge_sections(self, knowledge_context: KnowledgeContext) -> list[str]:
        sections: list[str] = []
        if knowledge_context.top_evidence:
            lines = ["Knowledge context (ranked evidence):"]
            lines.extend(
                f"  [{item.tier.name}] {item.doc_id}: {item.title} "
                f"(relevance={item.relevance_score:.2f})"
                for item in knowledge_context.top_evidence
            )
            sections.append("\n".join(lines))

        if knowledge_context.retrieval_hits:
            lines = ["Knowledge context (retrieval hits):"]
            lines.extend(
                f"  [{hit.tier}] {hit.doc_id}: {hit.title} "
                f"(score={hit.score:.3f}, subsystem={hit.subsystem})"
                for hit in knowledge_context.retrieval_hits
            )
            sections.append("\n".join(lines))

        if knowledge_context.ontology_hints:
            lines = ["Knowledge context (ontology hints):"]
            lines.extend(f"  - {hint}" for hint in knowledge_context.ontology_hints)
            sections.append("\n".join(lines))
        return sections

    def _history_section(self, history: tuple[NegotiationRound, ...]) -> str:
        if not history:
            return ""
        lines = ["\n\nPrior negotiation rounds:"]
        for item in history:
            status = "accepted" if item.accepted else "rejected"
            extras = []
            if item.crew_reduction:
                extras.append(f"crew_reduction={item.crew_reduction}")
            if item.dust_degradation_adjustment:
                extras.append(f"dust_adj={item.dust_degradation_adjustment:.3f}")
            extra_str = f" [{', '.join(extras)}]" if extras else ""
            lines.append(
                f"  Round {item.round_num + 1}: {item.reduction_fraction * 100:.1f}%"
                f" reduction{extra_str} -> {status}. {item.rationale}"
            )
        return "\n".join(lines)

    def _build_messages(
        self,
        goal: MissionGoal,
        conflicts: tuple[CrossDomainConflict, ...],
        current_reduction: float,
        history: tuple[NegotiationRound, ...],
        capabilities: tuple[SpecialistCapability, ...] = (),
        knowledge_context: KnowledgeContext | None = None,
    ) -> list[ChatCompletionMessageParam]:
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
            "You have three knobs to resolve Power deficits:\n"
            "  1. isru_reduction_fraction: float 0.0–0.8; reduces ISRU feedstock.\n"
            "  2. crew_reduction: integer 0–5; reduces ECLSS power demand.\n"
            "  3. dust_degradation_adjustment: float 0.0–0.1; improves solar generation.\n"
            "Return a JSON object conforming strictly to this format:\n"
            '{"accepted": bool, "isru_reduction_fraction": float, '
            '"crew_reduction": int, "dust_degradation_adjustment": float, '
            '"rationale": "string"}\n'
            "isru_reduction_fraction must be 0.0–0.8. "
            "crew_reduction must be 0–5. "
            "dust_degradation_adjustment must be 0.0–0.1."
        )

        system_prompt = self._append_capability_preferences(system_prompt, capabilities)
        system_prompt = self._append_knowledge_context(system_prompt, knowledge_context)
        history_section = self._history_section(history)

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
        capabilities: tuple[SpecialistCapability, ...] = (),
        knowledge_context: KnowledgeContext | None = None,
    ) -> tuple[bool, float, int, float, str]:
        """
        Executes a multi-agent negotiation loop to resolve specific conflicts.
        Returns (accepted, isru_reduction, crew_reduction, dust_adjustment, rationale).
        """
        if not self.is_enabled or not self.client:
            logger.debug("MultiAgentNegotiator is disabled or missing credentials. Skipping.")
            return False, current_reduction, 0, 0.0, "Negotiator disabled."

        logger.info("Triggering LLM multi-agent negotiation for %d conflicts.", len(conflicts))

        try:
            response = self.client.chat.completions.create(
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                messages=self._build_messages(
                    goal,
                    conflicts,
                    current_reduction,
                    history,
                    capabilities,
                    knowledge_context,
                ),
                response_format={"type": "json_object"},
                temperature=0.2,
            )

            raw_json = response.choices[0].message.content
            if not raw_json:
                return False, current_reduction, 0, 0.0, "Empty response from LLM."

            result_dict = json.loads(raw_json)
            result = NegotiationResult(**result_dict)

            return (
                result.accepted,
                result.isru_reduction_fraction,
                result.crew_reduction,
                result.dust_degradation_adjustment,
                result.rationale,
            )

        except Exception:
            logger.error("LLM negotiation failed.", exc_info=True)
            return False, current_reduction, 0, 0.0, "Negotiator error; see logs."
