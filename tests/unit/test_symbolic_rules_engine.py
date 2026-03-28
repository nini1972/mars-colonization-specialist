from mars_agent.reasoning import EqualityConstraint, SymbolicRulesEngine, UpperBoundConstraint


def test_symbolic_engine_reports_explicit_constraint_violations() -> None:
    engine = SymbolicRulesEngine(
        constraints=(
            EqualityConstraint(
                constraint_id="mass_balance.o2",
                left_key="o2_produced_kg",
                right_key="o2_consumed_kg",
                tolerance=0.1,
            ),
            UpperBoundConstraint(
                constraint_id="safety.co2_ppm",
                metric_key="co2_ppm",
                maximum=5000.0,
            ),
        )
    )

    violations = engine.validate(
        {
            "o2_produced_kg": 100.0,
            "o2_consumed_kg": 97.0,
            "co2_ppm": 6400.0,
        }
    )

    assert len(violations) == 2
    assert {item.constraint_id for item in violations} == {"mass_balance.o2", "safety.co2_ppm"}
    assert "tolerance" in violations[0].message or "tolerance" in violations[1].message
