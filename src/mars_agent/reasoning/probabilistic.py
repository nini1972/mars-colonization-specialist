"""Probabilistic forecasting contracts and baseline implementation."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol

from mars_agent.reasoning.models import ForecastRequest, ForecastResult


class ProbabilisticForecaster(Protocol):
    """Interface for uncertainty-aware forecasts used by specialist modules."""

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        """Return expected value and confidence interval for the metric."""


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("Cannot compute quantile on empty values")
    if q <= 0.0:
        return sorted_values[0]
    if q >= 1.0:
        return sorted_values[-1]

    index = (len(sorted_values) - 1) * q
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = index - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


@dataclass(slots=True)
class GaussianMonteCarloForecaster:
    """Deterministic Monte Carlo forecaster for baseline uncertainty modeling."""

    samples: int = 2048
    seed: int = 42

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        if not 0.0 < request.confidence_level < 1.0:
            raise ValueError("confidence_level must be between 0 and 1")
        if not request.inputs:
            raise ValueError("at least one uncertain input is required")
        if self.samples < 10:
            raise ValueError("samples must be >= 10")

        rng = random.Random(self.seed)
        draws: list[float] = []

        for _ in range(self.samples):
            total = 0.0
            for item in request.inputs:
                sample = rng.normalvariate(item.mean, item.std_dev)
                if item.minimum is not None:
                    sample = max(item.minimum, sample)
                if item.maximum is not None:
                    sample = min(item.maximum, sample)
                total += sample
            draws.append(total)

        draws.sort()
        alpha = (1.0 - request.confidence_level) / 2.0
        lower = _quantile(draws, alpha)
        upper = _quantile(draws, 1.0 - alpha)
        expected = sum(draws) / float(len(draws))

        assumptions = (
            "Inputs are independent and approximately Gaussian",
            "Interval is computed empirically from sampled distribution",
        )

        return ForecastResult(
            metric=request.metric,
            expected_value=expected,
            lower_bound=lower,
            upper_bound=upper,
            confidence_level=request.confidence_level,
            model_name="gaussian_monte_carlo_v1",
            assumptions=assumptions,
        )
