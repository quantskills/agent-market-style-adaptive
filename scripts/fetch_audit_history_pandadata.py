"""Download true PandaData audit opinions in quarter-sized requests."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
from pathlib import Path

import pandas as pd


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def quarters(start: str, end: str):
    value, terminal = pd.Period(start.upper(), freq="Q"), pd.Period(end.upper(), freq="Q")
    while value <= terminal:
        yield f"{value.year}q{value.quarter}"
        value += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2018q1")
    parser.add_argument("--end", default="2026q2")
    parser.add_argument("--output", default="data/audit_history_pandadata.parquet")
    args = parser.parse_args()
    import panda_data as pdx
    username = input("PandaData user: ")
    password = getpass.getpass("PandaData password: ")
    pdx.init_token(username, password)
    del password
    frames = []
    for quarter in quarters(args.start, args.end):
        frame = pdx.get_audit_opinion(start_quarter=quarter, end_quarter=quarter, market="cn")
        frames.append(frame)
        print(f"{quarter}: {len(frame)}", flush=True)
    audit = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    path = Path(args.output)
    audit.to_parquet(path, index=False, compression="zstd")
    manifest = {"source": "authenticated PandaData SDK", "start_quarter": args.start, "end_quarter": args.end,
                "total_rows": len(audit), "sha256": sha256(path), "local_path": str(path.resolve()), "truncated": False}
    path.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
