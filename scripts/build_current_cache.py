"""Build a fresh cache through the latest common stock/index/ETF date."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from agent_market_style_adaptive.data import validate_cache
from import_pandadata_lake import normalized_prices, read_dataset, yyyymmdd


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fund_adjusted(raw: pd.DataFrame, end: pd.Timestamp) -> pd.DataFrame:
    raw = raw[pd.to_datetime(raw["date"]) <= end].copy()
    raw = raw.sort_values(["symbol", "date"], kind="stable").drop_duplicates(["date", "symbol"], keep="last")
    factor = pd.to_numeric(raw["cum_adj_factor"], errors="coerce")
    terminal = raw.groupby("symbol", sort=False)["cum_adj_factor"].transform("last")
    relative = factor / pd.to_numeric(terminal, errors="coerce").replace(0, np.nan)
    for col in ("open", "high", "low", "close"):
        raw[f"adj_{col}"] = pd.to_numeric(raw[col], errors="coerce") * relative
    raw["adj_pre_close"] = raw.groupby("symbol", sort=False)["adj_close"].shift(1)
    first = raw["adj_pre_close"].isna()
    raw.loc[first, "adj_pre_close"] = pd.to_numeric(raw.loc[first, "pre_close"], errors="coerce") * relative[first]
    raw["date"] = pd.to_datetime(raw["date"])
    return raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=r"E:\量化实盘选股\data\lake")
    ap.add_argument("--fund", default="data/expanded_etf_raw/fund_daily.parquet")
    ap.add_argument("--destination", default="data/cache_current")
    ap.add_argument("--end", default="2026-08-27")
    args = ap.parse_args()
    source, dest, end = Path(args.source), Path(args.destination), pd.Timestamp(args.end)
    dest.mkdir(parents=True, exist_ok=True)
    start = 20180101
    raw = read_dataset(source, "bars", start, int(end.strftime("%Y%m%d")))
    post = read_dataset(source, "bars_post_adjusted", start, int(end.strftime("%Y%m%d")))
    normalized_prices(raw, post).to_parquet(dest / "stock_daily.parquet", index=False, compression="zstd")
    index = read_dataset(source, "index_bars", start, int(end.strftime("%Y%m%d")))
    index = index[index["symbol"].eq("000300.SH")].drop_duplicates(["date", "symbol"], keep="last")
    index["date"] = yyyymmdd(index["date"])
    index.to_parquet(dest / "index_daily.parquet", index=False, compression="zstd")
    fund_adjusted(pd.read_parquet(args.fund), end).to_parquet(dest / "fund_daily.parquet", index=False, compression="zstd")
    reports = read_dataset(source, "financial_reports")
    reports = reports[pd.to_numeric(reports["date"], errors="coerce") <= int(end.strftime("%Y%m%d"))]
    reports["announcement_date"] = yyyymmdd(reports["date"])
    reports["report_period"] = pd.PeriodIndex(reports["quarter"].astype(str).str.replace("q", "Q", regex=False), freq="Q").end_time.normalize()
    reports["revenue"] = reports["operating_revenue"]
    reports["net_profit"] = reports["np_parent_owners"]
    reports["bvps"] = reports["se_parent_owners"] / reports["total_shares"].replace(0, np.nan)
    reports["roe"] = reports["roe"].fillna(reports["np_parent_owners"] / reports["se_parent_owners"].replace(0, np.nan) * 100)
    reports[["symbol", "announcement_date", "report_period", "revenue", "roe", "total_assets", "total_shares", "bvps", "net_profit"]].drop_duplicates(["symbol", "announcement_date", "report_period"], keep="last").to_parquet(dest / "fundamentals.parquet", index=False, compression="zstd")
    div = read_dataset(source, "cash_dividends")
    div = div[pd.to_numeric(div["announcement_date"], errors="coerce") <= int(end.strftime("%Y%m%d"))].copy()
    div["effective_date"] = div.get("record_date")
    div["div_type"] = "cash"
    for col in ("announcement_date", "effective_date", "ex_date"):
        div[col] = yyyymmdd(div[col])
    div[["symbol", "announcement_date", "effective_date", "ex_date", "div_type"]].to_parquet(dest / "dividend.parquet", index=False, compression="zstd")
    details = read_dataset(source, "stock_details").rename(columns={"listed_date": "list_date", "de_listed_date": "delist_date", "board_type": "board"})
    details.loc[pd.to_numeric(details["delist_date"], errors="coerce").fillna(0).eq(0), "delist_date"] = None
    details["list_date"], details["delist_date"] = yyyymmdd(details["list_date"]), yyyymmdd(details["delist_date"])
    details[["symbol", "name", "list_date", "delist_date", "board"]].to_parquet(dest / "security_master.parquet", index=False, compression="zstd")
    for name in ("audit_opinion",):
        shutil.copy2(Path("data/cache") / f"{name}.parquet", dest / f"{name}.parquet")
    artifacts = [{"table": p.stem, "local_path": str(p.resolve()), "total_rows": pq.ParquetFile(p).metadata.num_rows, "sha256": sha256(p), "truncated": False} for p in sorted(dest.glob("*.parquet"))]
    (dest / "manifest.json").write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "source": str(source.resolve()), "source_kind": "PandaData-backed QuantBT lake plus authenticated PandaData ETF artifact", "effective_end_date": args.end, "artifacts": artifacts}, ensure_ascii=False, indent=2), encoding="utf-8")
    findings = validate_cache(dest)
    (dest / "validation.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(findings, ensure_ascii=False))
    return 0 if findings["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
