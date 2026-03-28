"""Static, physics, and scenario validation for generated simulations."""

from __future__ import annotations

from dataclasses import dataclass

from mars_agent.simulation.compiler import referenced_names
from mars_agent.simulation.ir import ModelSpec, ScenarioSpec


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A validation issue produced by static/physics/scenario checks."""

    code: str
    message: str


def validate_static(model_spec: ModelSpec) -> tuple[ValidationIssue, ...]:
    """Validate schema and symbol references before compilation."""

    issues: list[ValidationIssue] = []

    variable_names = [item.name for item in model_spec.variables]
    equation_outputs = [item.output for item in model_spec.equations]

    if len(variable_names) != len(set(variable_names)):
        issues.append(
            ValidationIssue("static.duplicate_variable", "Model has duplicate variable names.")
        )
    if len(equation_outputs) != len(set(equation_outputs)):
        issues.append(
            ValidationIssue("static.duplicate_output", "Model has duplicate equation outputs.")
        )

    known_names = set(variable_names)
    for equation in model_spec.equations:
        refs = referenced_names(equation.expression)
        unknown = sorted(ref for ref in refs if ref not in known_names)
        if unknown:
            issues.append(
                ValidationIssue(
                    "static.unknown_reference",
                    f"Equation {equation.output} references unknown names: {unknown}",
                )
            )
        known_names.add(equation.output)

    return tuple(issues)


def validate_physics(outputs: dict[str, float]) -> tuple[ValidationIssue, ...]:
    """Validate coarse conservation-style checks and operational margins."""

    issues: list[ValidationIssue] = []

    load_margin = outputs.get("load_margin_kw", 0.0)
    surplus = outputs.get("resource_surplus_ratio", 0.0)

    if load_margin < 0.0:
        issues.append(
            ValidationIssue(
                "physics.load_margin_negative",
                f"Load margin is negative ({load_margin:.2f} kW).",
            )
        )
    if surplus < 1.0:
        issues.append(
            ValidationIssue(
                "physics.resource_surplus_low",
                f"Resource surplus ratio is below one ({surplus:.3f}).",
            )
        )

    return tuple(issues)


def validate_scenario(
    scenario: ScenarioSpec,
    outputs: dict[str, float],
) -> tuple[ValidationIssue, ...]:
    """Validate scenario-specific thresholds."""

    issues: list[ValidationIssue] = []

    storage_cover = outputs.get("storage_cover_hours", 0.0)
    minimum_storage = 18.0

    if scenario.name in {"dust_storm", "resupply_delay"} and storage_cover < minimum_storage:
        issues.append(
            ValidationIssue(
                "scenario.storage_cover_low",
                (
                    f"Scenario {scenario.name} has insufficient storage cover "
                    f"({storage_cover:.2f} h < {minimum_storage:.2f} h)."
                ),
            )
        )

    return tuple(issues)
