from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

import pandas as pd

from agent_market_style_adaptive.backtest import Backtester
from agent_market_style_adaptive.config import config_hash, load_config
from agent_market_style_adaptive.metrics import performance_metrics


ETF_STRUCTURE = {
    "relative_strength_min_percentile": .50,
    "fixed_stop": .08,
    "trailing_stop": .10,
    "trailing_trigger": .05,
    "target_volatility": .15,
    "risk_budget_min_scale": .30,
}
BLUE_STRUCTURE = {
    "allowed_regimes": ["warm", "overheated"],
    "target_volatility": .15,
    "risk_budget_min_scale": .30,
}


def variants(base: dict) -> list[tuple[str, dict]]:
    baseline = deepcopy(base)
    structural = deepcopy(base)
    structural["etf"].update(ETF_STRUCTURE)
    structural["blue_chip"].update(BLUE_STRUCTURE)
    all_budget = deepcopy(structural)
    all_budget["small_cap"].update({"target_volatility": .30, "risk_budget_min_scale": .25})
    return [("baseline", baseline), ("etf_blue_structural", structural), ("all_sleeves_budget", all_budget)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/backtest_2018_cached.yaml")
    parser.add_argument("--output", default="outputs/structural_combination_development")
    parser.add_argument("--mode", choices=["static", "adaptive"], default="static")
    args = parser.parse_args()

    base = load_config(args.config)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    engine = Backtester.from_cache(base)
    rows = []
    for name, cfg in variants(base):
        cfg["run"]["end_date"] = "2024-12-31"
        cfg["run"]["effective_end_date"] = "2024-12-31"
        engine.config = cfg
        result = engine.run(args.mode)
        metrics = performance_metrics(result.equity.set_index("date")["nav"])
        rows.append({"candidate": name, "mode": args.mode, "config_hash": config_hash(cfg), **metrics})
        result.write(output / name)
        print(f"{name}: annual={metrics['annual_return']:.2%} dd={metrics['max_drawdown']:.2%}")
    leaderboard = pd.DataFrame(rows).sort_values(
        ["annual_return", "max_drawdown"], ascending=[False, False]
    )
    leaderboard.to_csv(output / f"{args.mode}_leaderboard.csv", index=False)
    (output / f"{args.mode}_manifest.json").write_text(
        json.dumps({"development_end": "2024-12-31", "holdout_start": "2025-01-01",
                    "candidates": [name for name, _ in variants(base)]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
