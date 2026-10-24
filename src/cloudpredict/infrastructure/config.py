"""Configuration loader: prices, tiers, margin, and generator defaults."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from cloudpredict.domain.ev import TierPrice
from cloudpredict.domain.models import Tier
from cloudpredict.infrastructure.generator import GeneratorParams

DEFAULT_CONFIG_PATH = Path("config/cloudpredict.toml")


@dataclass(frozen=True, slots=True)
class PricingMetadata:
    source: str
    region: str
    captured_on: str
    version: str


@dataclass(frozen=True, slots=True)
class Config:
    tiers: dict[Tier, TierPrice]
    min_ev_margin: Decimal
    scoring_horizon_days: int
    pricing: PricingMetadata
    generator: GeneratorParams


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    run = raw["run"]
    pricing = raw["pricing"]
    tiers: dict[Tier, TierPrice] = {}
    for tier_name, values in raw["tiers"].items():
        tiers[Tier(tier_name)] = TierPrice(
            storage_per_gb_month=Decimal(values["storage_per_gb_month"]),
            retrieval_per_gb=Decimal(values["retrieval_per_gb"]),
        )
    if set(tiers) != set(Tier):
        raise ValueError(f"config must define every Storage Tier, got {sorted(tiers)}")

    generator_raw = raw.get("generator", {})
    generator = GeneratorParams(
        n_objects=int(generator_raw.get("objects", 5000)),
        days=int(generator_raw.get("days", 180)),
        alpha=float(generator_raw.get("alpha", 1.1)),
        seed=int(generator_raw.get("seed", 42)),
        horizon_days=int(generator_raw.get("horizon_days", 30)),
        target_positive_fraction=float(generator_raw.get("target_positive_fraction", 0.20)),
    )

    if generator.horizon_days != int(run["scoring_horizon_days"]):
        raise ValueError(
            "[generator] horizon_days must equal [run] scoring_horizon_days: "
            "labels, calibration, and estimator training all share one Scoring Horizon"
        )

    return Config(
        tiers=tiers,
        min_ev_margin=Decimal(run["min_ev_margin"]),
        scoring_horizon_days=int(run["scoring_horizon_days"]),
        pricing=PricingMetadata(
            source=str(pricing["source"]),
            region=str(pricing["region"]),
            captured_on=str(pricing["captured_on"]),
            version=str(pricing["version"]),
        ),
        generator=generator,
    )
