"""Build a separate point-in-time cache with an expanded PandaData ETF universe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from agent_market_style_adaptive.data import validate_cache


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def link_or_copy(source: Path, destination: Path) -> None:
    if destination.exists():
        return
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-cache", default="data/cache")
    parser.add_argument("--raw", default="data/expanded_etf_raw/fund_daily.parquet")
    parser.add_argument("--destination", default="data/cache_expanded_etf")
    parser.add_argument("--effective-end", default="2026-05-29")
    args = parser.parse_args()
    source, raw_path, destination = Path(args.source_cache), Path(args.raw), Path(args.destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("stock_daily", "index_daily", "fundamentals", "audit_opinion", "dividend", "security_master"):
        link_or_copy(source / f"{name}.parquet", destination / f"{name}.parquet")

    end = pd.Timestamp(args.effective_end)
    raw = pd.read_parquet(raw_path)
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw[raw["date"] <= end].copy()
    raw = raw.sort_values(["symbol", "date"], kind="stable").drop_duplicates(["date", "symbol"], keep="last")
    factor = pd.to_numeric(raw["cum_adj_factor"], errors="coerce")
    terminal = raw.groupby("symbol", sort=False)["cum_adj_factor"].transform("last")
    relative = factor / pd.to_numeric(terminal, errors="coerce").replace(0, np.nan)
    for column in ("open", "high", "low", "close"):
        raw[f"adj_{column}"] = pd.to_numeric(raw[column], errors="coerce") * relative
    raw["adj_pre_close"] = raw.groupby("symbol", sort=False)["adj_close"].shift(1)
    first = raw["adj_pre_close"].isna()
    raw.loc[first, "adj_pre_close"] = pd.to_numeric(raw.loc[first, "pre_close"], errors="coerce") * relative[first]
    raw.to_parquet(destination / "fund_daily.parquet", index=False, compression="zstd")

    artifacts = []
    for path in sorted(destination.glob("*.parquet")):
        artifacts.append({"table": path.stem, "local_path": str(path.resolve()), "total_rows": pq.ParquetFile(path).metadata.num_rows,
                          "sha256": sha256(path), "truncated": False})
    manifest = {"source": "PandaData SDK ETF artifacts plus validated original cache", "effective_end_date": str(end.date()),
                "adjustment_factor_cutoff": str(end.date()), "artifacts": artifacts}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    findings = validate_cache(destination)
    if not findings["ok"]:
        raise SystemExit(json.dumps(findings, ensure_ascii=False))
    print(json.dumps({"ok": True, "fund_rows": len(raw), "fund_symbols": raw["symbol"].nunique(), "effective_end": str(end.date())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
