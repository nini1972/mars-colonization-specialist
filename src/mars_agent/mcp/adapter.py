"""Phase 7 MCP adapter exposing planner, simulation, and governance tools."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from enum import Enum

from mars_agent.governance import BenchmarkHarness, GovernanceGate
from mars_agent.knowledge.models import TrustTier
from mars_agent.orchestration import CentralPlanner, MissionGoal, MissionPhase
from mars_agent.orchestration.models import PlanResult
from mars_agent.reasoning.models import EvidenceReference
from mars_agent.simulation import SimulationPipeline
from mars_agent.simulation.pipeline import SimulationReport

type MCPScalar = str | int | float | bool | None
type MCPValue = MCPScalar | list["MCPValue"] | dict[str, "MCPValue"]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Minimal MCP-like tool descriptor for adapter discoverability."""

    name: str
    description: str
    required_arguments: tuple[str, ...]


def to_mcp_value(value: object) -> MCPValue:
    """Convert typed Python/dataclass objects into MCP-serializable values."""

    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        enum_value = value.value
        if isinstance(enum_value, (str, int, float, bool)):
            return enum_value
        raise TypeError(f"Unsupported enum value type: {type(enum_value).__name__}")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return {
            item.name: to_mcp_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): to_mcp_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_mcp_value(item) for item in value]

    raise TypeError(f"Unsupported value for MCP serialization: {type(value).__name__}")


def _require_mapping(arguments: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = arguments.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"Argument '{key}' must be an object")
    return value


def _require_string(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Argument '{key}' must be a non-empty string")
    return value


def _optional_float(arguments: Mapping[str, object], key: str, default: float) -> float:
    value = arguments.get(key, default)
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(f"Argument '{key}' must be numeric")


def _optional_int(arguments: Mapping[str, object], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, int):
        return value
    raise ValueError(f"Argument '{key}' must be an integer")


def _parse_trust_tier(raw: object) -> TrustTier:
    if isinstance(raw, int):
        return TrustTier(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if text.isdigit():
            return TrustTier(int(text))
        return TrustTier[text]
    raise ValueError("Evidence tier must be an integer value or enum name")


def _parse_goal(raw_goal: Mapping[str, object]) -> MissionGoal:
    mission_id = raw_goal.get("mission_id")
    if not isinstance(mission_id, str) or not mission_id:
        raise ValueError("goal.mission_id must be a non-empty string")

    current_phase_raw = raw_goal.get("current_phase")
    if not isinstance(current_phase_raw, str):
        raise ValueError("goal.current_phase must be a mission phase string")

    crew_size = raw_goal.get("crew_size")
    if not isinstance(crew_size, int):
        raise ValueError("goal.crew_size must be an integer")

    def number(name: str) -> float:
        value = raw_goal.get(name)
        if isinstance(value, (int, float)):
            return float(value)
        raise ValueError(f"goal.{name} must be numeric")

    desired = raw_goal.get("desired_confidence", 0.9)
    if not isinstance(desired, (int, float)):
        raise ValueError("goal.desired_confidence must be numeric")

    return MissionGoal(
        mission_id=mission_id,
        crew_size=crew_size,
        horizon_years=number("horizon_years"),
        current_phase=MissionPhase(current_phase_raw),
        solar_generation_kw=number("solar_generation_kw"),
        battery_capacity_kwh=number("battery_capacity_kwh"),
        dust_degradation_fraction=number("dust_degradation_fraction"),
        hours_without_sun=number("hours_without_sun"),
        desired_confidence=float(desired),
    )


def _parse_evidence(arguments: Mapping[str, object]) -> tuple[EvidenceReference, ...]:
    raw_evidence = arguments.get("evidence")
    if not isinstance(raw_evidence, Sequence) or isinstance(raw_evidence, (str, bytes, bytearray)):
        raise ValueError("Argument 'evidence' must be an array")

    parsed: list[EvidenceReference] = []
    for index, item in enumerate(raw_evidence):
        if not isinstance(item, Mapping):
            raise ValueError(f"evidence[{index}] must be an object")

        source_id = item.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError(f"evidence[{index}].source_id must be a non-empty string")

        doc_id = item.get("doc_id")
        if not isinstance(doc_id, str) or not doc_id:
            raise ValueError(f"evidence[{index}].doc_id must be a non-empty string")

        title = item.get("title")
        if not isinstance(title, str) or not title:
            raise ValueError(f"evidence[{index}].title must be a non-empty string")

        url = item.get("url")
        if not isinstance(url, str) or not url:
            raise ValueError(f"evidence[{index}].url must be a non-empty string")

        relevance = item.get("relevance_score")
        if not isinstance(relevance, (int, float)):
            raise ValueError(f"evidence[{index}].relevance_score must be numeric")

        tier_raw = item.get("tier")
        if tier_raw is None:
            raise ValueError(f"evidence[{index}].tier is required")

        published_on_raw = item.get("published_on")
        if not isinstance(published_on_raw, str):
            raise ValueError(f"evidence[{index}].published_on must be an ISO date string")

        parsed.append(
            EvidenceReference(
                source_id=source_id,
                doc_id=doc_id,
                title=title,
                url=url,
                published_on=date.fromisoformat(published_on_raw),
                tier=_parse_trust_tier(tier_raw),
                relevance_score=float(relevance),
            )
        )

    return tuple(parsed)


@dataclass(slots=True)
class MarsMCPAdapter:
    """MCP-compatible facade over deterministic planning and safety pipelines."""

    planner: CentralPlanner = field(default_factory=CentralPlanner)
    simulation_pipeline: SimulationPipeline = field(default_factory=SimulationPipeline)
    governance_gate: GovernanceGate = field(default_factory=GovernanceGate)
    benchmark_harness: BenchmarkHarness = field(default_factory=BenchmarkHarness)
    _plans: dict[str, PlanResult] = field(default_factory=dict, init=False, repr=False)
    _simulations: dict[str, SimulationReport] = field(default_factory=dict, init=False, repr=False)

    def list_tools(self) -> tuple[ToolDefinition, ...]:
        """List exposed tool contracts for MCP hosts."""

        return (
            ToolDefinition(
                name="mars.plan",
                description="Generate a mission plan from goal and evidence inputs.",
                required_arguments=("goal", "evidence"),
            ),
            ToolDefinition(
                name="mars.simulate",
                description=(
                    "Run deterministic simulation pipeline for a previously generated plan."
                ),
                required_arguments=("plan_id",),
            ),
            ToolDefinition(
                name="mars.governance",
                description="Evaluate governance gate using plan and simulation outputs.",
                required_arguments=("plan_id", "simulation_id"),
            ),
            ToolDefinition(
                name="mars.benchmark",
                description="Evaluate benchmark deltas using plan and simulation outputs.",
                required_arguments=("plan_id", "simulation_id"),
            ),
        )

    def invoke(self, tool_name: str, arguments: Mapping[str, object]) -> dict[str, MCPValue]:
        """Invoke a tool method using MCP-like JSON arguments."""

        if tool_name == "mars.plan":
            goal = _parse_goal(_require_mapping(arguments, "goal"))
            evidence = _parse_evidence(arguments)
            return self._plan(goal=goal, evidence=evidence)

        if tool_name == "mars.simulate":
            plan_id = _require_string(arguments, "plan_id")
            seed = _optional_int(arguments, "seed", self.simulation_pipeline.seed)
            max_repair_attempts = _optional_int(
                arguments,
                "max_repair_attempts",
                self.simulation_pipeline.max_repair_attempts,
            )
            return self._simulate(
                plan_id=plan_id,
                seed=seed,
                max_repair_attempts=max_repair_attempts,
            )

        if tool_name == "mars.governance":
            plan_id = _require_string(arguments, "plan_id")
            simulation_id = _require_string(arguments, "simulation_id")
            min_confidence = _optional_float(
                arguments,
                "min_confidence",
                self.governance_gate.min_confidence,
            )
            return self._governance(
                plan_id=plan_id,
                simulation_id=simulation_id,
                min_confidence=min_confidence,
            )

        if tool_name == "mars.benchmark":
            plan_id = _require_string(arguments, "plan_id")
            simulation_id = _require_string(arguments, "simulation_id")
            return self._benchmark(plan_id=plan_id, simulation_id=simulation_id)

        raise KeyError(f"Unsupported MCP tool: {tool_name}")

    def _plan(
        self,
        goal: MissionGoal,
        evidence: tuple[EvidenceReference, ...],
    ) -> dict[str, MCPValue]:
        plan = self.planner.plan(goal=goal, evidence=evidence)
        plan_id = f"plan-{len(self._plans) + 1:04d}"
        self._plans[plan_id] = plan
        return {
            "plan_id": plan_id,
            "plan": to_mcp_value(plan),
        }

    def _simulate(
        self,
        plan_id: str,
        seed: int,
        max_repair_attempts: int,
    ) -> dict[str, MCPValue]:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise KeyError(f"Unknown plan_id: {plan_id}")

        pipeline = SimulationPipeline(seed=seed, max_repair_attempts=max_repair_attempts)
        simulation = pipeline.run(plan)
        simulation_id = f"simulation-{len(self._simulations) + 1:04d}"
        self._simulations[simulation_id] = simulation
        return {
            "simulation_id": simulation_id,
            "simulation": to_mcp_value(simulation),
        }

    def _governance(
        self,
        plan_id: str,
        simulation_id: str,
        min_confidence: float,
    ) -> dict[str, MCPValue]:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise KeyError(f"Unknown plan_id: {plan_id}")

        simulation = self._simulations.get(simulation_id)
        if simulation is None:
            raise KeyError(f"Unknown simulation_id: {simulation_id}")

        report = GovernanceGate(min_confidence=min_confidence).evaluate(
            plan=plan,
            simulation=simulation,
        )
        return {
            "governance": to_mcp_value(report),
        }

    def _benchmark(self, plan_id: str, simulation_id: str) -> dict[str, MCPValue]:
        plan = self._plans.get(plan_id)
        if plan is None:
            raise KeyError(f"Unknown plan_id: {plan_id}")

        simulation = self._simulations.get(simulation_id)
        if simulation is None:
            raise KeyError(f"Unknown simulation_id: {simulation_id}")

        report = self.benchmark_harness.run(plan=plan, simulation=simulation)
        return {
            "benchmark": to_mcp_value(report),
        }