from mars_agent.reasoning import ForecastInput, ForecastRequest, GaussianMonteCarloForecaster


def test_forecaster_returns_confidence_interval() -> None:
    forecaster = GaussianMonteCarloForecaster(samples=4000, seed=7)
    request = ForecastRequest(
        metric="daily_power_demand_kwh",
        confidence_level=0.9,
        inputs=(
            ForecastInput(name="eclss", mean=60.0, std_dev=3.0, minimum=50.0),
            ForecastInput(name="isru", mean=90.0, std_dev=6.0, minimum=70.0),
        ),
    )

    result = forecaster.forecast(request)

    assert result.metric == "daily_power_demand_kwh"
    assert result.lower_bound <= result.expected_value <= result.upper_bound
    assert result.confidence_level == 0.9
    assert result.model_name
    assert len(result.assumptions) >= 1
