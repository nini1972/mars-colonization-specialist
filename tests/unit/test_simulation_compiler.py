from mars_agent.simulation import (
    EquationSpec,
    InterfaceSpec,
    ModelSpec,
    VariableSpec,
    compile_model,
)


def test_compiler_executes_model_and_exposes_outputs() -> None:
    model = ModelSpec(
        model_id="sim-test",
        variables=(
            VariableSpec("a", "kW", 10.0),
            VariableSpec("b", "kW", 4.0),
        ),
        equations=(
            EquationSpec("load", "a + b"),
            EquationSpec("margin", "a - b"),
        ),
        assumptions=("test model",),
        interface=InterfaceSpec(inputs=("a", "b"), outputs=("load", "margin")),
    )

    artifact = compile_model(model)
    outputs = artifact.run()

    assert outputs["load"] == 14.0
    assert outputs["margin"] == 6.0
    assert "run_simulation" in artifact.source_code
