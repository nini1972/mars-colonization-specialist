"""Generate-test-repair simulation pipeline for Phase 5."""

from __future__ import annotations

from dataclasses import dataclass

from mars_agent.orchestration.models import PlanResult
from mars_agent.simulation.compiler import compile_model
from mars_agent.simulation.ir import EquationSpec, InterfaceSpec, ModelSpec, VariableSpec
from mars_agent.simulation.scenarios import default_scenarios
from mars_agent.simulation.validation import (
    ValidationIssue,
    validate_physics,
    validate_scenario,
    validate_static,
)


@dataclass(frozen=True, slots=True)
class ScenarioRunResult:
    """Result of executing one scenario in the simulation pipeline."""

    scenario_name: str
    passed: bool
    outputs: dict[str, float]
    issues: tuple[ValidationIssue, ...]
    remediation: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SimulationReport:
    """Aggregate report for one generate-test-repair simulation cycle."""

    model_id: str
    seed: int
    attempts: int
    scenarios: tuple[ScenarioRunResult, ...]


@dataclass(slots=True)
class SimulationPipeline:
    """Builds simulation IR from planning output and executes repair loops."""

    seed: int = 42
    max_repair_attempts: int = 2

    def _extract_metric(self, plan: PlanResult, metric_name: str) -> float:
        for response in plan.subsystem_responses:
            for metric in response.metrics:
                if metric.name == metric_name:
                    return metric.value.mean
        raise KeyError(f"Metric not found in plan output: {metric_name}")

    def _model_from_plan(self, plan: PlanResult) -> ModelSpec:
        eclss_load = self._extract_metric(plan, "eclss_power_demand_kw")
        isru_load = self._extract_metric(plan, "isru_power_demand_kw")
        effective_generation = self._extract_metric(plan, "effective_generation_kw")
        storage_cover_hours = self._extract_metric(plan, "storage_cover_hours")

        variables = (
            VariableSpec("eclss_power_demand_kw", "kW", eclss_load),
            VariableSpec("isru_power_demand_kw", "kW", isru_load),
            VariableSpec("effective_generation_kw", "kW", effective_generation),
            VariableSpec("storage_cover_hours", "h", storage_cover_hours),
            VariableSpec("reserve_factor", "ratio", 1.0),
        )

        equations = (
            EquationSpec(
                "critical_load_kw",
                "(eclss_power_demand_kw + isru_power_demand_kw) * reserve_factor",
            ),
            EquationSpec(
                "load_margin_kw",
                "effective_generation_kw - critical_load_kw",
            ),
            EquationSpec(
                "resource_surplus_ratio",
                "effective_generation_kw / max(critical_load_kw, 1e-6)",
            ),
        )

        return ModelSpec(
            model_id=f"sim-{plan.mission_id}",
            variables=variables,
            equations=equations,
            assumptions=(
                "Phase 4 specialist metrics are the baseline operating state",
                "Scenario modifiers represent multiplicative perturbations",
            ),
            interface=InterfaceSpec(
                inputs=tuple(item.name for item in variables),
                outputs=(
                    "critical_load_kw",
                    "load_margin_kw",
                    "resource_surplus_ratio",
                    "storage_cover_hours",
                ),
            ),
        )

    def _repair_model(self, model_spec: ModelSpec) -> ModelSpec:
        repaired_variables: list[VariableSpec] = []
        for item in model_spec.variables:
            if item.name == "isru_power_demand_kw":
                repaired_variables.append(
                    VariableSpec(item.name, item.unit, item.initial_value * 0.85)
                )
                continue
            if item.name == "effective_generation_kw":
                repaired_variables.append(
                    VariableSpec(item.name, item.unit, item.initial_value * 1.1)
                )
                continue
            if item.name == "storage_cover_hours":
                repaired_variables.append(
                    VariableSpec(item.name, item.unit, item.initial_value * 1.1)
                )
                continue
            repaired_variables.append(item)

        return ModelSpec(
            model_id=model_spec.model_id,
            variables=tuple(repaired_variables),
            equations=model_spec.equations,
            assumptions=model_spec.assumptions,
            interface=model_spec.interface,
        )

    def _remediation_for_issue(self, issue: ValidationIssue) -> str:
        if issue.code in {"physics.load_margin_negative", "physics.resource_surplus_low"}:
            return "Reduce ISRU throughput and add dispatchable power reserve"
        if issue.code == "scenario.storage_cover_low":
            return "Increase battery capacity or reduce storm-window critical loads"
        return "Review model assumptions and rerun with updated baseline"

    def _run_scenarios(self, model_spec: ModelSpec) -> tuple[ScenarioRunResult, ...]:
        artifact = compile_model(model_spec)
        results: list[ScenarioRunResult] = []

        for scenario in default_scenarios():
            outputs = artifact.run(scenario_modifiers=scenario.modifiers)
            issues = (
                *validate_physics(outputs),
                *validate_scenario(scenario, outputs),
            )
            remediation = tuple(dict.fromkeys(self._remediation_for_issue(item) for item in issues))
            results.append(
                ScenarioRunResult(
                    scenario_name=scenario.name,
                    passed=not issues,
                    outputs=outputs,
                    issues=issues,
                    remediation=remediation,
                )
            )

        return tuple(results)

    def run(self, plan: PlanResult) -> SimulationReport:
        model_spec = self._model_from_plan(plan)

        attempts = 0
        static_issues = validate_static(model_spec)
        while static_issues and attempts < self.max_repair_attempts:
            attempts += 1
            model_spec = self._repair_model(model_spec)
            static_issues = validate_static(model_spec)

        scenario_runs = self._run_scenarios(model_spec)
        while (
            any(not run.passed for run in scenario_runs)
            and attempts < self.max_repair_attempts
        ):
            attempts += 1
            model_spec = self._repair_model(model_spec)
            scenario_runs = self._run_scenarios(model_spec)

        return SimulationReport(
            model_id=model_spec.model_id,
            seed=self.seed,
            attempts=attempts,
            scenarios=scenario_runs,
        )
