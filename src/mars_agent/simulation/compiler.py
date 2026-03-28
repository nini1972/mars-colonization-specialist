"""Compiler that turns simulation IR into executable Python artifacts."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from mars_agent.simulation.ir import ModelSpec

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ALLOWED_NAMES = {"max", "min", "abs"}


def referenced_names(expression: str) -> set[str]:
    """Return symbol names referenced by an equation expression."""

    candidates = set(_IDENTIFIER.findall(expression))
    return {name for name in candidates if not name.isnumeric() and name not in _ALLOWED_NAMES}


def _safe_eval(expression: str, env: dict[str, float]) -> float:
    tree = ast.parse(expression, mode="eval")
    compiled = compile(tree, filename="<simulation-ir>", mode="eval")
    scope: dict[str, object] = {name: env[name] for name in env}
    scope.update({"max": max, "min": min, "abs": abs})
    return float(eval(compiled, {"__builtins__": {}}, scope))


@dataclass(frozen=True, slots=True)
class SimulationArtifact:
    """Executable artifact produced by compiling a model specification."""

    model_id: str
    source_code: str
    model_spec: ModelSpec

    def run(self, scenario_modifiers: dict[str, float] | None = None) -> dict[str, float]:
        env = {item.name: item.initial_value for item in self.model_spec.variables}

        if scenario_modifiers is not None:
            for name, factor in scenario_modifiers.items():
                if name in env:
                    env[name] *= factor

        for equation in self.model_spec.equations:
            env[equation.output] = _safe_eval(equation.expression, env)

        return {name: env[name] for name in self.model_spec.interface.outputs}


def compile_model(model_spec: ModelSpec) -> SimulationArtifact:
    """Compile IR to a deterministic executable simulation artifact."""

    lines = [f"# Auto-generated simulation artifact for {model_spec.model_id}"]
    lines.append("def run_simulation(env):")
    for equation in model_spec.equations:
        lines.append(f"    env['{equation.output}'] = {equation.expression}")
    lines.append("    return env")

    return SimulationArtifact(
        model_id=model_spec.model_id,
        source_code="\n".join(lines),
        model_spec=model_spec,
    )
