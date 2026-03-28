from mars_agent.simulation import EquationSpec, InterfaceSpec, ModelSpec, VariableSpec
from mars_agent.simulation.validation import validate_static


def test_static_validation_catches_unknown_references() -> None:
    model = ModelSpec(
        model_id="sim-bad",
        variables=(VariableSpec("known", "kW", 1.0),),
        equations=(EquationSpec("derived", "known + missing_symbol"),),
        assumptions=("bad static model",),
        interface=InterfaceSpec(inputs=("known",), outputs=("derived",)),
    )

    issues = validate_static(model)

    assert issues
    assert any(item.code == "static.unknown_reference" for item in issues)
