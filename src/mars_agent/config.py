"""Configuration loading utilities for deterministic project bootstrap."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BaselineConfig:
    """Minimal configuration schema required for Phase 0 health checks."""

    app_name: str
    random_seed: int


def load_baseline_config(path: Path) -> BaselineConfig:
    """Load and validate minimal baseline config from TOML."""
    with path.open("rb") as handle:
        data = tomllib.load(handle)

    app_name = data.get("app", {}).get("name")
    random_seed = data.get("runtime", {}).get("random_seed")

    if not isinstance(app_name, str) or not app_name:
        raise ValueError("app.name must be a non-empty string")

    if not isinstance(random_seed, int):
        raise ValueError("runtime.random_seed must be an integer")

    return BaselineConfig(app_name=app_name, random_seed=random_seed)
