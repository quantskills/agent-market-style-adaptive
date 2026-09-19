"""Compare discrete market-risk / rebalance review frequencies.

Daily stop exits remain active in both variants.  Only state review, selection,
risk budgeting and normal target rebalancing are delayed to the configured
review window.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import yaml

from agent_market_style_adaptive.backtest import Backtester
from agent_market_style_adaptive.config import config_hash, load_config
from agent_market_style_adaptive.metrics import performance_metrics


def _metrics(result, annualization: int) -> dict:
    frame = result.equity.set_index("date")
    metrics = performance_metrics(frame["nav"], frame["benchmark"], annualization)
    trades = result.trades
    metrics["transaction_cost"] = float(trades["cost"].sum()) if not trades.empty else 0.0
    metrics["turnover"] = float(trades["gross"].sum() / frame["nav"].mean()) if not trades.empty else 0.0
    metrics["trade_days"] = int(trades["date"].nunique()) if not trades.empty else 0
    metrics["trades"] = int(len(trades))
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/optimized.pre2025.yaml")
    parser.add_argument("--output", default="outputs/rebalance_frequency_comparison")
    parser.add_argument("--selected-config", default="config/optimized.rebalance.yaml")
    args = parser.parse_args()
    base, output = load_config(args.config), Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    engine = Backtester.from_cache(base)
    rows, candidates = [], {}
    for frequency in ("weekly", "semi_monthly"):
        cfg = deepcopy(base)
        cfg["rebalance"] = {"frequency": frequency, "daily_stops_enabled": True}
        cfg["data"]["output_dir"] = str(output / frequency)
        engine.config = cfg
        for mode in ("static", "adaptive"):
            result = engine.run(mode)
            result.write(output / frequency)
            rows.append({"frequency": frequency, "mode": mode, "config_hash": config_hash(cfg), **_metrics(result, int(cfg["run"]["annualization_days"]))})
        candidates[frequency] = cfg
    leaderboard = pd.DataFrame(rows).sort_values(["mode", "annual_return"], ascending=[True, False])
    leaderboard.to_csv(output / "frequency_leaderboard.csv", index=False)
    # The user selected annualized return as the primary objective.  Adaptive
    # is the production Agent mode; static remains an explicit comparator.
    adaptive = leaderboard[leaderboard["mode"].eq("adaptive")]
    winner = adaptive.sort_values(["annual_return", "annual_excess_return"], ascending=False).iloc[0]
    selected = candidates[str(winner["frequency"])]
    selected["data"]["output_dir"] = "outputs/backtest_2018_rebalance_" + str(winner["frequency"])
    selected["optimization"]["rebalance_frequency_selection"] = {
        "primary_objective": "adaptive full-period annual return",
        "winner": str(winner["frequency"]),
        "daily_stops_enabled": True,
        "leaderboard": "outputs/rebalance_frequency_comparison/frequency_leaderboard.csv",
    }
    Path(args.selected_config).write_text(yaml.safe_dump(selected, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (output / "selection_summary.json").write_text(json.dumps({"winner": str(winner["frequency"]), "config_hash": config_hash(selected)}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(leaderboard.to_string(index=False))
    print(f"winner={winner['frequency']} config={args.selected_config}")


if __name__ == "__main__":
    main()
