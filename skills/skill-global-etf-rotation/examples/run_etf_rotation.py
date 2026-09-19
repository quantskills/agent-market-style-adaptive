"""Run the global ETF rotation Skill independently against the Parquet cache."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from agent_market_style_adaptive.config import load_config
from qs_global_etf_rotation import select_etfs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", required=True, help="Signal date, YYYY-MM-DD")
    parser.add_argument("--cache-dir", default="data/cache")
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--output", default="outputs/skill-etf-rotation")
    args = parser.parse_args()

    signal_date = pd.Timestamp(args.as_of).normalize()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    config = load_config(args.config)
    history = pd.read_parquet(
        Path(args.cache_dir) / "fund_daily.parquet",
        filters=[("date", "<=", signal_date)],
    )
    result = select_etfs(signal_date, history, config["etf"])
    result.evidence.to_csv(output / "evidence.csv", index=False)
    payload = {"as_of": str(signal_date.date()), "selected": result.symbols, "selected_count": len(result.symbols)}
    (output / "selection.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output / "selection.json")


if __name__ == "__main__":
    main()
