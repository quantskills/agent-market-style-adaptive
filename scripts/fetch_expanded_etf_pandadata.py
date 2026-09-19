"""Fetch an expanded ETF universe from an authenticated PandaData session.

The script deliberately prompts for credentials instead of accepting them via
arguments.  Nothing secret is written to the workspace.  Each request covers
at most one calendar year, matching the PandaData API limit, and outputs
unmodified local Parquet artifacts plus a hash manifest for later materialization.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
import yaml


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def yearly_ranges(start: pd.Timestamp, end: pd.Timestamp):
    left = start
    while left <= end:
        right = min(pd.Timestamp(year=left.year, month=12, day=31), end)
        yield left, right
        left = right + pd.Timedelta(days=1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/expanded_etf_universe.yaml")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--output", default="data/expanded_etf_raw")
    args = parser.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    symbols = list(cfg["etf"]["symbols"])
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    if end < start:
        raise SystemExit("end date precedes start date")

    import panda_data as pdx
    username = input("PandaData user: ")
    password = getpass.getpass("PandaData password: ")
    pdx.init_token(username, password)
    del password

    raw_frames, pre_frames = [], []
    for left, right in yearly_ranges(start, end):
        params = {"start_date": left.strftime("%Y%m%d"), "end_date": right.strftime("%Y%m%d"), "symbol": symbols}
        raw = pdx.get_fund_daily(**params)
        pre = pdx.get_fund_daily_pre(**params)
        raw_frames.append(raw)
        pre_frames.append(pre)
        print(f"{left.date()}..{right.date()}: raw={len(raw)} pre={len(pre)}", flush=True)

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for label, frames in (("fund_daily", raw_frames), ("fund_daily_pre", pre_frames)):
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if frame.empty:
            raise SystemExit(f"PandaData returned no rows for {label}")
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.sort_values(["symbol", "date"], kind="stable").drop_duplicates(["date", "symbol"], keep="last")
        path = output / f"{label}.parquet"
        frame.to_parquet(path, index=False, compression="zstd")
        artifacts.append({"table": label, "local_path": str(path.resolve()), "total_rows": len(frame), "sha256": digest(path), "truncated": False})
    manifest = {"source": "authenticated PandaData SDK", "start_date": str(start.date()), "end_date": str(end.date()),
                "symbols": symbols, "artifacts": artifacts}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "artifacts": [{"table": x["table"], "rows": x["total_rows"]} for x in artifacts]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
