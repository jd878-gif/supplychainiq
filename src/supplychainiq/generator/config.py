"""Load and validate the generator configuration."""

from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    validate_config(cfg)
    return cfg


def validate_config(cfg: dict) -> None:
    for tier, dist in cfg["delay_distribution"].items():
        total = sum(dist.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"delay_distribution.{tier} sums to {total}, expected 1.0")
        if any(int(k) < 0 for k in dist):
            raise ValueError(f"delay_distribution.{tier} has negative delays")
    for tier, fr in cfg["fill_rate"].items():
        lo, hi = fr["short_range"]
        if not (0 <= fr["full_prob"] <= 1 and 0 <= lo <= hi <= 1):
            raise ValueError(f"fill_rate.{tier} values must be within [0, 1]")
    if not 0 <= cfg["suppliers"]["unreliable_share"] <= 1:
        raise ValueError("suppliers.unreliable_share must be within [0, 1]")
