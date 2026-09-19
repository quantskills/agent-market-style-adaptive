"""Fetch point-in-time index constituents in bounded PandaData chunks.

The API returns a large panel for a long date range.  This script deliberately
chunks by year and writes immutable Parquet artifacts; credentials are entered
interactively and are never persisted.
"""
from __future__ import annotations

import argparse
import getpass
import time
from pathlib import Path

import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2026-09-18")
    parser.add_argument("--output", default="data/index_weights_pandadata")
    parser.add_argument("--indices", nargs="+", default=["000852.SH", "000300.SH", "399101.SZ"])
    args = parser.parse_args()
    import panda_data as pdx

    user = input("PandaData user: ")
    password = getpass.getpass("PandaData password: ")
    pdx.init_token(user, password)
    del password
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    for index_symbol in args.indices:
        pieces = []
        for year in range(start.year, end.year + 1):
            left = max(start, pd.Timestamp(year=year, month=1, day=1))
            right = min(end, pd.Timestamp(year=year, month=12, day=31))
            if left > right:
                continue
            path = output / f"{index_symbol.replace('.', '_')}_{left:%Y%m%d}_{right:%Y%m%d}.parquet"
            if path.exists():
                frame = pd.read_parquet(path)
            else:
                for attempt in range(4):
                    try:
                        frame = pdx.get_index_weights(index_symbol=index_symbol, start_date=left.strftime("%Y%m%d"), end_date=right.strftime("%Y%m%d"))
                        break
                    except Exception:
                        if attempt == 3:
                            raise
                        time.sleep(3 * (attempt + 1))
                frame.to_parquet(path, index=False, compression="zstd")
            pieces.append(frame)
            print(index_symbol, left.date(), right.date(), len(frame), flush=True)
        all_rows = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
        if not all_rows.empty:
            all_rows["date"] = pd.to_datetime(all_rows["date"], errors="coerce")
            all_rows = all_rows.drop_duplicates(["index_symbol", "stock_symbol", "date"], keep="last").sort_values(["date", "stock_symbol"])
            all_rows.to_parquet(output / f"{index_symbol.replace('.', '_')}.parquet", index=False, compression="zstd")
            print("TOTAL", index_symbol, len(all_rows), all_rows["date"].min().date(), all_rows["date"].max().date(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
