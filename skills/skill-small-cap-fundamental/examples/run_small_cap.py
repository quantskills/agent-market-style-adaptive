"""Run the small-cap Skill independently against the canonical Parquet cache."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from agent_market_style_adaptive.config import load_config
from qs_small_cap_fundamental import select_small_cap


def read_table(cache: Path, name: str, filters=None) -> pd.DataFrame:
    return pd.read_parquet(cache / f"{name}.parquet", filters=filters)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, help="Signal date, YYYY-MM-DD")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--output", default="outputs/skill-small-cap")
    args = parser.parse_args()

    signal_date = pd.Timestamp(args.as_of).normalize()
    cache, output = Path(args.cache_dir), Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config)

    snapshot = read_table(cache, "stock_daily", [("date", "==", signal_date)])
    fundamentals = read_table(cache, "fundamentals", [("announcement_date", "<", signal_date)])
    audit = read_table(cache, "audit_opinion", [("date", "<", signal_date)])
    lookback = signal_date - pd.DateOffset(years=int(config["small_cap"]["dividend_lookback_years"]))
    dividends = read_table(cache, "dividend", [[("announcement_date", ">=", lookback), ("announcement_date", "<", signal_date)]])
    benchmark = config["run"]["benchmark"]
    index_history = read_table(cache, "index_daily", [[("symbol", "==", benchmark), ("date", "<=", signal_date)]])

    result = select_small_cap(signal_date, snapshot, fundamentals, audit, dividends, index_history, config["small_cap"])
    result.evidence.to_csv(output / "evidence.csv", index=False)
    payload = {"as_of": str(signal_date.date()), "selected": result.symbols, "selected_count": len(result.symbols)}
    (output / "selection.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output / "selection.json")


if __name__ == "__main__":
    main()
