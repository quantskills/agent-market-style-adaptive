from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


def load_config(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("configuration root must be a mapping")
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    for section in ("run", "data", "small_cap", "etf", "blue_chip", "regime", "allocation", "risk", "costs"):
        if section not in config:
            raise ValueError(f"missing configuration section: {section}")
    for name, weights in config["allocation"].items():
        total = sum(float(value) for value in weights.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"allocation {name} sums to {total}, expected 1")
        if any(float(value) < 0 for value in weights.values()):
            raise ValueError(f"allocation {name} contains a negative weight")
    if len(config["etf"]["momentum_windows"]) != len(config["etf"]["momentum_weights"]):
        raise ValueError("ETF momentum windows and weights must have equal length")


def config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

