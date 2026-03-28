from mars_agent.randomness import deterministic_sample


def test_deterministic_sample_is_stable() -> None:
    first = deterministic_sample(424242)
    second = deterministic_sample(424242)
    assert first == second
