"""Multi-agent negotiation module for resolving cross-domain coupling constraints."""

import asyncio
import importlib
import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, Field

from mars_agent.orchestration.models import (
    CrossDomainConflict,
    KnowledgeContext,
    MissionGoal,
)
from mars_agent.specialists.contracts import SpecialistCapability
from mars_agent.specialists.contracts import TradeoffProposal

type ChatCompletionMessageParamT = dict[str, str]

try:
    _openai = importlib.import_module("openai")
    OpenAI = _openai.OpenAI
    AsyncOpenAI = _openai.AsyncOpenAI
    HAS_OPENAI = True
except (ImportError, AttributeError):
    HAS_OPENAI = False
    AsyncOpenAI = None
    OpenAI = None

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
        self.is_async_enabled = (
            os.environ.get("MARS_LLM_ORCHESTRATOR_ASYNC", "false").lower() == "true"
        )
        self.use_responses_api = (
            os.environ.get("MARS_LLM_OPENAI_USE_RESPONSES", "true").lower() == "true"
        )
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.client = OpenAI(api_key=self.api_key) if HAS_OPENAI and self.api_key else None
        self.async_client = (
            AsyncOpenAI(api_key=self.api_key)
            if HAS_OPENAI and self.api_key
            else None
        )
        # TODO(phase-11): Migrate Chat Completions calls to the OpenAI Responses API.
        # Reference: https://developers.openai.com/api/docs/guides/migrate-to-responses

    @staticmethod
    def _disabled_result(current_reduction: float) -> tuple[bool, float, int, float, str]:
        return False, current_reduction, 0, 0.0, "Negotiator disabled."

    @staticmethod
    def _result_from_json(
        raw_json: str,
        current_reduction: float,
    ) -> tuple[bool, float, int, float, str]:
        result_dict = json.loads(MultiAgentNegotiator._extract_json_object(raw_json))
        result = NegotiationResult(**result_dict)
        return (
            result.accepted,
            result.isru_reduction_fraction,
            result.crew_reduction,
            result.dust_degradation_adjustment,
            result.rationale,
        )

    @staticmethod
    def _extract_json_object(raw_text: str) -> str:
        text = raw_text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\\s*", "", text)
            text = re.sub(r"\\s*```$", "", text)

        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                candidate = text[start : end + 1]
                json.loads(candidate)
                return candidate
            raise

    @staticmethod
    def _extract_text_value(value: object) -> str | None:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else None
        return None

    def _extract_responses_output_text(self, response: object) -> str | None:
        output_text = self._extract_text_value(getattr(response, "output_text", None))
        if output_text is not None:
            return output_text

        if isinstance(response, Mapping):
            mapping_text = self._extract_text_value(response.get("output_text"))
            if mapping_text is not None:
                return mapping_text

        output = getattr(response, "output", None)
        if output is None and isinstance(response, Mapping):
            output = response.get("output")
        if not isinstance(output, list):
            return None

        chunks: list[str] = []
        for item in output:
            content = getattr(item, "content", None)
            if content is None and isinstance(item, Mapping):
                content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                text_value = getattr(part, "text", None)
                if text_value is None and isinstance(part, Mapping):
                    text_value = part.get("text")
                resolved = self._extract_text_value(text_value)
                if resolved is not None:
                    chunks.append(resolved)
        return "\n".join(chunks) if chunks else None

    def _extract_chat_completions_output_text(self, response: object) -> str | None:
        choices = getattr(response, "choices", None)
        if isinstance(choices, list) and choices:
            first = choices[0]
            message = getattr(first, "message", None)
            content = getattr(message, "content", None)
            resolved = self._extract_text_value(content)
            if resolved is not None:
                return resolved

        if isinstance(response, Mapping):
            choices_raw = response.get("choices")
            if isinstance(choices_raw, list) and choices_raw:
                first = choices_raw[0]
                if isinstance(first, Mapping):
                    message = first.get("message")
                    if isinstance(message, Mapping):
                        resolved = self._extract_text_value(message.get("content"))
                        if resolved is not None:
                            return resolved
        return None

    def _build_chat_completions_kwargs(
        self,
        messages: list[ChatCompletionMessageParamT],
    ) -> dict[str, object]:
        return {
            "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

    def _build_responses_kwargs(
        self,
        messages: list[ChatCompletionMessageParamT],
    ) -> dict[str, object]:
        return {
            "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            "input": messages,
            "temperature": 0.2,
        }

    def _responses_enabled(self) -> bool:
        return bool(getattr(self, "use_responses_api", True))

    def _negotiate_via_chat_completions(
        self,
        messages: list[ChatCompletionMessageParamT],
        current_reduction: float,
    ) -> tuple[bool, float, int, float, str]:
        if self.client is None:
            return self._disabled_result(current_reduction)
        client = self.client

        response = client.chat.completions.create(
            **self._build_chat_completions_kwargs(messages)
        )
        raw_json = self._extract_chat_completions_output_text(response)
        if not raw_json:
            return False, current_reduction, 0, 0.0, "Empty response from LLM."
        return self._result_from_json(raw_json, current_reduction)

    async def _negotiate_via_chat_completions_async(
        self,
        messages: list[ChatCompletionMessageParamT],
        current_reduction: float,
    ) -> tuple[bool, float, int, float, str]:
        if self.async_client is None:
            return self._disabled_result(current_reduction)
        async_client = self.async_client

        response = await async_client.chat.completions.create(
            **self._build_chat_completions_kwargs(messages)
        )
        raw_json = self._extract_chat_completions_output_text(response)
        if not raw_json:
            return False, current_reduction, 0, 0.0, "Empty response from LLM."
        return self._result_from_json(raw_json, current_reduction)

    def _negotiate_via_responses(
        self,
        messages: list[ChatCompletionMessageParamT],
        current_reduction: float,
    ) -> tuple[bool, float, int, float, str]:
        if self.client is None:
            return self._disabled_result(current_reduction)
        client = self.client

        response = client.responses.create(**self._build_responses_kwargs(messages))
        raw_json = self._extract_responses_output_text(response)
        if not raw_json:
            return False, current_reduction, 0, 0.0, "Empty response from LLM."
        return self._result_from_json(raw_json, current_reduction)

    async def _negotiate_via_responses_async(
        self,
        messages: list[ChatCompletionMessageParamT],
        current_reduction: float,
    ) -> tuple[bool, float, int, float, str]:
        if self.async_client is None:
            return self._disabled_result(current_reduction)
        async_client = self.async_client

        response = await async_client.responses.create(
            **self._build_responses_kwargs(messages)
        )
        raw_json = self._extract_responses_output_text(response)
        if not raw_json:
            return False, current_reduction, 0, 0.0, "Empty response from LLM."
        return self._result_from_json(raw_json, current_reduction)

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

    def _proposal_section(self, proposals: tuple[TradeoffProposal, ...]) -> str:
        supported_knobs = {
            "isru_reduction_fraction",
            "crew_reduction",
            "dust_degradation_adjustment",
        }
        relevant = [proposal for proposal in proposals if proposal.knob_name in supported_knobs]
        if not relevant:
            return ""

        lines = ["\n\nSpecialist-authored tradeoff proposals:"]
        for proposal in relevant:
            lines.append(
                f"  [{proposal.subsystem.name}] {proposal.knob_name} "
                f"delta={proposal.suggested_delta:.3f} -> {proposal.rationale}"
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
        proposals: tuple[TradeoffProposal, ...] = (),
    ) -> list[ChatCompletionMessageParamT]:
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
        proposal_section = self._proposal_section(proposals)

        user_prompt = (
            f"Mission Goal: {goal.crew_size} crew, Phase: {goal.current_phase.name}.\n"
            f"Current ISRU feedstock reduction is {current_reduction * 100:.1f}%.\n"
            "The CouplingChecker returned these conflicts:\n"
            + "\n".join(conflict_descriptions)
            + history_section
            + proposal_section
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
        proposals: tuple[TradeoffProposal, ...] = (),
    ) -> tuple[bool, float, int, float, str]:
        """
        Executes a multi-agent negotiation loop to resolve specific conflicts.
        Returns (accepted, isru_reduction, crew_reduction, dust_adjustment, rationale).
        """
        if not self.is_enabled or not self.client:
            logger.debug("MultiAgentNegotiator is disabled or missing credentials. Skipping.")
            return self._disabled_result(current_reduction)

        if self.is_async_enabled and self.async_client is not None:
            try:
                async_result = asyncio.run(
                    self.negotiate_async(
                        goal=goal,
                        conflicts=conflicts,
                        current_reduction=current_reduction,
                        history=history,
                        capabilities=capabilities,
                        knowledge_context=knowledge_context,
                        proposals=proposals,
                    )
                )
                if async_result[4] not in {
                    "Negotiator error; see logs.",
                    "Empty response from LLM.",
                }:
                    return async_result
                logger.warning("Async negotiation returned error; falling back to sync path.")
            except Exception:
                logger.error("Async negotiation failed; falling back to sync path.", exc_info=True)

        logger.info("Triggering LLM multi-agent negotiation for %d conflicts.", len(conflicts))

        try:
            messages = self._build_messages(
                goal,
                conflicts,
                current_reduction,
                history,
                capabilities,
                knowledge_context,
                proposals,
            )

            if self._responses_enabled() and hasattr(self.client, "responses"):
                try:
                    return self._negotiate_via_responses(messages, current_reduction)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Responses API negotiation failed; falling back to Chat Completions.",
                        exc_info=True,
                    )

            return self._negotiate_via_chat_completions(messages, current_reduction)

        except Exception:
            logger.error("LLM negotiation failed.", exc_info=True)
            return False, current_reduction, 0, 0.0, "Negotiator error; see logs."

    async def negotiate_async(
        self,
        goal: MissionGoal,
        conflicts: tuple[CrossDomainConflict, ...],
        current_reduction: float,
        history: tuple[NegotiationRound, ...] = (),
        capabilities: tuple[SpecialistCapability, ...] = (),
        knowledge_context: KnowledgeContext | None = None,
        proposals: tuple[TradeoffProposal, ...] = (),
    ) -> tuple[bool, float, int, float, str]:
        """Async variant of negotiate() used when async mode is enabled."""
        if not self.is_enabled or self.async_client is None:
            logger.debug("Async negotiator is disabled or missing credentials. Skipping.")
            return self._disabled_result(current_reduction)

        logger.info(
            "Triggering ASYNC LLM multi-agent negotiation for %d conflicts.",
            len(conflicts),
        )

        try:
            messages = self._build_messages(
                goal,
                conflicts,
                current_reduction,
                history,
                capabilities,
                knowledge_context,
                proposals,
            )

            if self._responses_enabled() and hasattr(self.async_client, "responses"):
                try:
                    return await self._negotiate_via_responses_async(messages, current_reduction)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Async Responses API negotiation failed; falling back to Chat Completions.",
                        exc_info=True,
                    )

            return await self._negotiate_via_chat_completions_async(messages, current_reduction)

        except Exception:
            logger.error("Async LLM negotiation failed.", exc_info=True)
            return False, current_reduction, 0, 0.0, "Negotiator error; see logs."
