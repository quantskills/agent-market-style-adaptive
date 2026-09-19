"""Materialize point-in-time reference tables from the local PandaData lake.

The source lake is read-only.  The generated files are small, immutable inputs
to the backtester and carry a manifest with coverage and hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_hive(root: Path, name: str) -> pd.DataFrame:
    return ds.dataset(root / "raw" / name, format="parquet", partitioning="hive").to_table().to_pandas()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=r"E:\量化实盘选股\data\lake")
    parser.add_argument("--output", default="data/cache_pandadata_20260918")
    parser.add_argument("--end", default="2026-09-18")
    args = parser.parse_args()
    source, output, end = Path(args.source), Path(args.output), pd.Timestamp(args.end)
    output.mkdir(parents=True, exist_ok=True)

    float_frame = read_hive(source, "share_float")
    float_frame["date"] = pd.to_datetime(float_frame["date"].astype(str), format="%Y%m%d", errors="coerce")
    float_frame = float_frame.loc[float_frame["date"].le(end), ["symbol", "date", "free_circulation"]]
    float_frame = float_frame.dropna(subset=["symbol", "date", "free_circulation"])
    float_frame = float_frame.loc[float_frame["free_circulation"].gt(0)].drop_duplicates(["symbol", "date"], keep="last")
    float_frame = float_frame.sort_values(["symbol", "date"], kind="stable")
    float_path = output / "share_float_reference.parquet"
    float_frame.to_parquet(float_path, index=False, compression="zstd")

    details = pd.read_parquet(source / "raw" / "stock_details" / "data.parquet")
    details = details.rename(columns={"listed_date": "list_date", "de_listed_date": "delist_date", "board_type": "board"})
    details["list_date"] = pd.to_datetime(details["list_date"].astype(str), format="%Y%m%d", errors="coerce")
    details["delist_date"] = pd.to_datetime(details["delist_date"].replace(0, pd.NA).astype("string"), format="%Y%m%d", errors="coerce")
    security = details[["symbol", "name", "list_date", "delist_date", "board", "sector_code", "sector_code_name"]].copy()
    security = security.dropna(subset=["symbol", "list_date"]).drop_duplicates("symbol", keep="last")
    security_path = output / "security_master_extended.parquet"
    security.to_parquet(security_path, index=False, compression="zstd")

    manifest = {
        "source": str(source.resolve()),
        "source_kind": "local PandaData-backed QuantBT lake",
        "requested_end": str(end.date()),
        "share_float_coverage_end": str(float_frame["date"].max().date()) if not float_frame.empty else None,
        "artifacts": [
            {"path": str(float_path.resolve()), "rows": len(float_frame), "sha256": sha256(float_path)},
            {"path": str(security_path.resolve()), "rows": len(security), "sha256": sha256(security_path)},
        ],
    }
    (output / "reference_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
