from pathlib import Path

from mars_agent.config import load_baseline_config


def test_load_baseline_config() -> None:
    cfg = load_baseline_config(Path("configs/baseline.toml"))
    assert cfg.app_name == "mars-colonization-agent"
    assert cfg.random_seed == 424242
