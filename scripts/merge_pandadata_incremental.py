"""Merge authenticated incremental artifacts into a new immutable cache."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from agent_market_style_adaptive.data import validate_cache


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def merge_prices(existing: Path, raw_path: Path, adjusted_path: Path, output: Path) -> None:
    old, raw, adjusted = pd.read_parquet(existing), pd.read_parquet(raw_path), pd.read_parquet(adjusted_path)
    for frame in (raw, adjusted):
        frame["date"] = pd.to_datetime(frame["date"])
    adj = adjusted[["date", "symbol", "open", "high", "low", "close", "pre_close"]].rename(
        columns={name: f"adj_{name}" for name in ("open", "high", "low", "close", "pre_close")}
    )
    new = raw.merge(adj, on=["date", "symbol"], how="left", validate="one_to_one")
    columns = list(old.columns)
    for column in columns:
        if column not in new:
            new[column] = pd.NA
    merged = pd.concat([old, new[columns]], ignore_index=True)
    merged = merged.sort_values(["date", "symbol"], kind="stable").drop_duplicates(["date", "symbol"], keep="last")
    merged.to_parquet(output, index=False, compression="zstd")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="data/cache_current")
    parser.add_argument("--increment", default="data/pandadata_incremental_raw_20260918")
    parser.add_argument("--output", default="data/cache_pandadata_20260918")
    args = parser.parse_args()
    base, inc, out = Path(args.base), Path(args.increment), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    merge_prices(base / "stock_daily.parquet", inc / "stock_daily.parquet", inc / "stock_daily_pre.parquet", out / "stock_daily.parquet")
    merge_prices(base / "fund_daily.parquet", inc / "fund_daily.parquet", inc / "fund_daily_pre.parquet", out / "fund_daily.parquet")
    old_idx, new_idx = pd.read_parquet(base / "index_daily.parquet"), pd.read_parquet(inc / "index_daily.parquet")
    old_idx["date"], new_idx["date"] = pd.to_datetime(old_idx["date"]), pd.to_datetime(new_idx["date"])
    pd.concat([old_idx, new_idx], ignore_index=True).sort_values(["date", "symbol"]).drop_duplicates(["date", "symbol"], keep="last").to_parquet(out / "index_daily.parquet", index=False, compression="zstd")
    for table in ("fundamentals", "audit_opinion", "security_master"):
        shutil.copy2(base / f"{table}.parquet", out / f"{table}.parquet")
    old_div, new_div = pd.read_parquet(base / "dividend.parquet"), pd.read_parquet(inc / "dividend.parquet")
    for column in ("announcement_date", "effective_date", "ex_date"):
        if column in old_div:
            old_div[column] = pd.to_datetime(old_div[column], errors="coerce")
        if column in new_div:
            new_div[column] = pd.to_datetime(new_div[column], errors="coerce")
    div = pd.concat([old_div, new_div[[c for c in old_div.columns if c in new_div.columns]]], ignore_index=True)
    div.drop_duplicates().to_parquet(out / "dividend.parquet", index=False, compression="zstd")
    artifacts = [{"table": p.stem, "local_path": str(p.resolve()), "total_rows": pq.ParquetFile(p).metadata.num_rows,
                  "sha256": digest(p), "truncated": False} for p in sorted(out.glob("*.parquet"))]
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "source": "authenticated PandaData SDK incremental merge",
                "effective_end_date": "2026-09-18", "artifacts": artifacts}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    findings = validate_cache(out)
    (out / "validation.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(findings, ensure_ascii=False))
    return 0 if findings["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
