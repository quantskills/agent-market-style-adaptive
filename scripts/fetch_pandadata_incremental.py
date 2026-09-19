"""Fetch post-cache PandaData artifacts for a point-in-time cache refresh.

Credentials are prompted interactively and never accepted as command-line
arguments.  The result is raw, hash-verified input for a later cache
materialization step; it intentionally does not mutate an existing cache.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import time
from pathlib import Path

import pandas as pd


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def weekly_ranges(start: pd.Timestamp, end: pd.Timestamp):
    left = start
    while left <= end:
        right = min(left + pd.Timedelta(days=6), end)
        yield left, right
        left = right + pd.Timedelta(days=1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-05-30")
    parser.add_argument("--end", default="2026-09-16")
    parser.add_argument("--output", default="data/pandadata_incremental_raw")
    args = parser.parse_args()
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    if end < start:
        raise SystemExit("end date precedes start date")
    output = Path(args.output)
    parts = output / "parts"
    parts.mkdir(parents=True, exist_ok=True)

    import panda_data as pdx
    username = input("PandaData user: ")
    password = getpass.getpass("PandaData password: ")
    pdx.init_token(username, password)
    del password

    tables: dict[str, list[pd.DataFrame]] = {"stock_daily": [], "stock_daily_pre": []}
    for left, right in weekly_ranges(start, end):
        params = {"start_date": left.strftime("%Y%m%d"), "end_date": right.strftime("%Y%m%d")}
        for label, reader in (("stock_daily", pdx.get_stock_daily), ("stock_daily_pre", pdx.get_stock_daily_pre)):
            path = parts / f"{label}_{left:%Y%m%d}_{right:%Y%m%d}.parquet"
            if path.exists():
                frame = pd.read_parquet(path)
            else:
                for attempt in range(3):
                    try:
                        frame = reader(**params)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(3 * (attempt + 1))
                frame.to_parquet(path, index=False, compression="zstd")
            tables[label].append(frame)
            print(f"{label} {left.date()}..{right.date()}: {len(frame)}", flush=True)

    tables["index_daily"] = [pdx.get_index_daily(symbol="000300.SH", start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))]
    etfs = ["518880.SH", "513100.SH", "159915.SZ", "510210.SH", "159920.SZ", "515000.SH", "511010.SH"]
    tables["fund_daily"] = [pdx.get_fund_daily(symbol=etfs, start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))]
    tables["fund_daily_pre"] = [pdx.get_fund_daily_pre(symbol=etfs, start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))]
    tables["audit_opinion"] = [pdx.get_audit_opinion(start_quarter="2026q2", end_quarter="2026q3", market="cn")]
    tables["dividend"] = [pdx.get_stock_dividend(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"), market="cn")]
    # The full reference table can exceed the gateway response limit. Keep the
    # already downloaded market/fundamental artifacts usable when this optional
    # call is interrupted; downstream validation will mark the table missing.
    try:
        tables["security_master"] = [pdx.get_stock_detail(status=None)]
    except Exception as exc:
        print(f"warning: security_master fetch failed: {exc}", flush=True)
    fields = ["symbol", "info_date", "end_date", "operating_revenue", "net_profit_parent", "total_assets", "total_shares", "roe_diluted", "bvps"]
    tables["fundamentals"] = [pdx.get_fina_performance(end_quarter=quarter, fields=fields) for quarter in ("2026q1", "2026q2")]

    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for label, frames in tables.items():
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if frame.empty:
            print(f"warning: {label} returned no rows", flush=True)
            continue
        path = output / f"{label}.parquet"
        frame.to_parquet(path, index=False, compression="zstd")
        artifacts.append({"table": label, "local_path": str(path.resolve()), "total_rows": len(frame), "sha256": sha256(path), "truncated": False})
        print(f"{label}: {len(frame)}", flush=True)
    (output / "manifest.json").write_text(json.dumps({"source": "authenticated PandaData SDK", "start_date": str(start.date()),
        "end_date": str(end.date()), "artifacts": artifacts}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
