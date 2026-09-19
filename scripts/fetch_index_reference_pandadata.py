"""Fetch point-in-time reference indices and monthly constituent snapshots.

The JoinQuant reference strategy uses CSI1000/CSI300/ChiNext index universes.
PandaData exposes both daily index prices and dated index weights.  This script
keeps the authenticated token in the process only, writes immutable parquet
artifacts, and never stores credentials in the repository.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd


INDICES = ("000300.SH", "399100.SZ", "399101.SZ")


def main() -> int:
    import panda_data as pdx

    username = os.environ.get("PANDA_DATA_USERNAME") or input("PandaData user: ")
    password = os.environ.get("PANDA_DATA_PASSWORD")
    if not password:
        import getpass

        password = getpass.getpass("PandaData password: ")
    pdx.init_token(username, password)
    del password

    cache = Path("data/cache_pandadata_20260918")
    weights_root = Path("data/index_weights_pandadata")
    weights_root.mkdir(parents=True, exist_ok=True)
    stock = pd.read_parquet(cache / "stock_daily.parquet", columns=["date"])
    dates = pd.to_datetime(stock["date"], errors="coerce").dropna()
    month_ends = dates.groupby(dates.dt.to_period("M")).max().sort_values()

    # Add the two style/reference indices needed by the copied white-horse
    # signal.  Keep the existing CSI300 rows and deduplicate by date/symbol.
    index_path = cache / "index_daily.parquet"
    existing = pd.read_parquet(index_path)
    pieces = [existing]
    for symbol in INDICES[1:]:
        try:
            symbol_pieces = []
            # PandaData limits index_daily requests to five years.
            for left, right in (("20180101", "20221231"), ("20230101", "20260918")):
                frame = pdx.get_index_daily(symbol=symbol, start_date=left, end_date=right)
                symbol_pieces.append(frame)
            frame = pd.concat(symbol_pieces, ignore_index=True)
            pieces.append(frame)
            print(f"index_daily {symbol}: {len(frame)} rows", flush=True)
        except Exception as exc:
            print(f"warning: index_daily {symbol} failed: {type(exc).__name__}: {exc}", flush=True)
    index_daily = pd.concat(pieces, ignore_index=True)
    index_daily["date"] = pd.to_datetime(index_daily["date"], errors="coerce")
    index_daily = index_daily.drop_duplicates(["date", "symbol"], keep="last").sort_values(["date", "symbol"])
    index_daily.to_parquet(index_path, index=False, compression="zstd")
    print(f"index_daily combined: {len(index_daily)} rows", flush=True)

    for symbol in INDICES:
        pieces = []
        safe = symbol.replace(".", "_")
        for date in month_ends:
            date_text = pd.Timestamp(date).strftime("%Y%m%d")
            target = weights_root / f"{safe}_{date_text}.parquet"
            if target.exists():
                frame = pd.read_parquet(target)
            else:
                for attempt in range(4):
                    try:
                        frame = pdx.get_index_weights(index_symbol=symbol, start_date=date_text, end_date=date_text)
                        break
                    except Exception:
                        if attempt == 3:
                            raise
                        time.sleep(2 * (attempt + 1))
                frame.to_parquet(target, index=False, compression="zstd")
            if not frame.empty:
                pieces.append(frame)
            print(f"weights {symbol} {date_text}: {len(frame)} rows", flush=True)
        combined = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
        if not combined.empty:
            combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
            combined = combined.drop_duplicates(["index_symbol", "stock_symbol", "date"], keep="last")
            combined = combined.sort_values(["date", "stock_symbol"])
            combined.to_parquet(weights_root / f"{safe}.parquet", index=False, compression="zstd")
            print(f"weights {symbol} combined: {len(combined)} rows", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
