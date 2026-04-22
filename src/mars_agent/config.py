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


@dataclass(frozen=True)
class BenchmarkReferenceConfig:
    """One configured benchmark reference entry."""

    metric: str
    target: float
    tolerance: float
    source: str


@dataclass(frozen=True)
class BenchmarkPolicyProfileConfig:
    """Named benchmark policy profile loaded from TOML."""

    name: str
    policy_version: str
    policy_source: str
    references: tuple[BenchmarkReferenceConfig, ...]


@dataclass(frozen=True)
class ReleasePolicyConfig:
    """Knowledge release policy config including benchmark profiles."""

    default_benchmark_profile: str
    benchmark_profiles: tuple[BenchmarkPolicyProfileConfig, ...]


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


def load_release_policy_config(path: Path) -> ReleasePolicyConfig:
    """Load release policy config including named benchmark profiles."""

    with path.open("rb") as handle:
        data = tomllib.load(handle)

    benchmark = data.get("benchmark")
    if not isinstance(benchmark, dict):
        raise ValueError("benchmark section must be present")

    default_profile = benchmark.get("default_profile")
    if not isinstance(default_profile, str) or not default_profile:
        raise ValueError("benchmark.default_profile must be a non-empty string")

    raw_profiles = benchmark.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ValueError("benchmark.profiles must contain at least one profile")

    profiles: list[BenchmarkPolicyProfileConfig] = []
    for name, raw_profile in raw_profiles.items():
        if not isinstance(name, str) or not name:
            raise ValueError("benchmark profile names must be non-empty strings")
        if not isinstance(raw_profile, dict):
            raise ValueError(f"benchmark.profiles.{name} must be a table")

        policy_version = raw_profile.get("policy_version")
        policy_source = raw_profile.get("policy_source")
        if not isinstance(policy_version, str) or not policy_version:
            raise ValueError(f"benchmark.profiles.{name}.policy_version must be a string")
        if not isinstance(policy_source, str) or not policy_source:
            raise ValueError(f"benchmark.profiles.{name}.policy_source must be a string")

        raw_references = raw_profile.get("references")
        if not isinstance(raw_references, list) or not raw_references:
            raise ValueError(f"benchmark.profiles.{name}.references must contain entries")

        references: list[BenchmarkReferenceConfig] = []
        for index, raw_reference in enumerate(raw_references):
            if not isinstance(raw_reference, dict):
                raise ValueError(
                    f"benchmark.profiles.{name}.references[{index}] must be a table"
                )
            metric = raw_reference.get("metric")
            source = raw_reference.get("source")
            target = raw_reference.get("target")
            tolerance = raw_reference.get("tolerance")
            if not isinstance(metric, str) or not metric:
                raise ValueError(
                    f"benchmark.profiles.{name}.references[{index}].metric must be a string"
                )
            if not isinstance(source, str) or not source:
                raise ValueError(
                    f"benchmark.profiles.{name}.references[{index}].source must be a string"
                )
            if not isinstance(target, (int, float)):
                raise ValueError(
                    f"benchmark.profiles.{name}.references[{index}].target must be numeric"
                )
            if not isinstance(tolerance, (int, float)):
                raise ValueError(
                    f"benchmark.profiles.{name}.references[{index}].tolerance must be numeric"
                )
            references.append(
                BenchmarkReferenceConfig(
                    metric=metric,
                    target=float(target),
                    tolerance=float(tolerance),
                    source=source,
                )
            )

        profiles.append(
            BenchmarkPolicyProfileConfig(
                name=name,
                policy_version=policy_version,
                policy_source=policy_source,
                references=tuple(references),
            )
        )

    if default_profile not in {profile.name for profile in profiles}:
        raise ValueError("benchmark.default_profile must refer to a configured profile")

    return ReleasePolicyConfig(
        default_benchmark_profile=default_profile,
        benchmark_profiles=tuple(profiles),
    )
