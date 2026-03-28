"""Deterministic randomness utilities used by tests and runtime bootstrap."""

from __future__ import annotations

import random


def set_global_seed(seed: int) -> None:
    """Set global pseudo-random seed for deterministic behavior."""
    random.seed(seed)


def deterministic_sample(seed: int) -> list[int]:
    """Produce deterministic sample output used by smoke tests."""
    rng = random.Random(seed)
    return [rng.randint(0, 10_000) for _ in range(5)]
