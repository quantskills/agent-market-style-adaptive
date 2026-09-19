"""Run the low-volatility blue-chip Skill independently against the cache."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from agent_market_style_adaptive.config import load_config
from qs_low_vol_blue_chip import select_blue_chips


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, help="Signal date, YYYY-MM-DD")
    parser.add_argument("--sentiment", choices=("cold", "warm", "overheated"), default="warm")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--output", default="outputs/skill-blue-chip")
    args = parser.parse_args()

    signal_date = pd.Timestamp(args.as_of).normalize()
    config = load_config(args.config)
    window = int(config["blue_chip"]["volatility_window"])
    history_start = signal_date - pd.DateOffset(days=max(180, window * 3))
    cache = Path(args.cache_dir)
    history = pd.read_parquet(
        cache / "stock_daily.parquet",
        filters=[[('date', '>=', history_start), ('date', '<=', signal_date)]],
    ).sort_values(["symbol", "date"], kind="stable")
    price = "adj_close" if "adj_close" in history else "close"
    returns = history.groupby("symbol", sort=False)[price].pct_change(fill_method=None)
    history["volatility"] = (
        returns.groupby(history["symbol"], sort=False)
        .rolling(window - 1, min_periods=window - 1)
        .std()
        .reset_index(level=0, drop=True)
        * np.sqrt(252)
    )
    snapshot = history[history["date"].eq(signal_date)].copy()
    fundamentals = pd.read_parquet(
        cache / "fundamentals.parquet",
        filters=[("announcement_date", "<", signal_date)],
    )

    result = select_blue_chips(signal_date, snapshot, fundamentals, args.sentiment, config["blue_chip"])
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    result.evidence.to_csv(output / "evidence.csv", index=False)
    payload = {"as_of": str(signal_date.date()), "sentiment": args.sentiment, "selected": result.symbols,
               "selected_count": len(result.symbols)}
    (output / "selection.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output / "selection.json")


if __name__ == "__main__":
    main()
