from pathlib import Path

from mars_agent.config import load_baseline_config, load_release_policy_config


def test_load_baseline_config() -> None:
    cfg = load_baseline_config(Path("configs/baseline.toml"))
    assert cfg.app_name == "mars-colonization-agent"
    assert cfg.random_seed == 424242


def test_load_release_policy_config() -> None:
    cfg = load_release_policy_config(Path("configs/knowledge_release_policy.toml"))

    assert cfg.default_benchmark_profile == "nasa-esa-mission-review"
    names = {item.name for item in cfg.benchmark_profiles}
    assert "nasa-esa-mission-review" in names
    assert "nasa-esa-mission-review-permissive" in names
