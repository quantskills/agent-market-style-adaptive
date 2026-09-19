"""Build this agent's canonical cache from an existing PandaData-backed QuantBT lake.

The source lake remains read-only. ETF and audit rows may be supplied as JSON
artifacts captured from the authenticated PandaData MCP gateway.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from agent_market_style_adaptive.config import load_config
from agent_market_style_adaptive.data import DataContractError, sha256_file, validate_cache


def yyyymmdd(values: pd.Series) -> pd.Series:
    text = values.astype("string").str.replace(r"\.0$", "", regex=True)
    return pd.to_datetime(text, format="%Y%m%d", errors="coerce")


def read_dataset(root: Path, name: str, start: int | None = None, end: int | None = None) -> pd.DataFrame:
    dataset = ds.dataset(root / "raw" / name, format="parquet", partitioning="hive")
    condition = None
    if start is not None:
        condition = ds.field("date") >= start
    if end is not None:
        upper = ds.field("date") <= end
        condition = upper if condition is None else condition & upper
    table = dataset.to_table(filter=condition)
    frame = table.to_pandas()
    return frame.drop(columns=[column for column in ("year", "month") if column in frame], errors="ignore")


def normalized_prices(raw: pd.DataFrame, post: pd.DataFrame) -> pd.DataFrame:
    keys = ["date", "symbol"]
    adjusted = post[keys + ["open", "high", "low", "close"]].rename(
        columns={column: f"post_{column}" for column in ("open", "high", "low", "close")}
    )
    data = raw.merge(adjusted, on=keys, how="inner", validate="one_to_one")
    # Hive partitions are scanned in ascending year/month order and the source
    # ingestion preserves ascending dates. Avoid sorting ten million rows again.
    last = data.loc[data.groupby("symbol", sort=False)["date"].idxmax()]
    scale = (last["post_close"] / last["close"]).replace([np.inf, -np.inf, 0], np.nan)
    scale_by_symbol = pd.Series(scale.to_numpy(), index=last["symbol"]).to_dict()
    divisor = data["symbol"].map(scale_by_symbol)
    for column in ("open", "high", "low", "close"):
        data[f"adj_{column}"] = data[f"post_{column}"] / divisor
    data["adj_pre_close"] = data.groupby("symbol", sort=False)["adj_close"].shift(1)
    first = data["adj_pre_close"].isna()
    data.loc[first, "adj_pre_close"] = data.loc[first, "pre_close"] * (
        data.loc[first, "adj_close"] / data.loc[first, "close"]
    )
    data["date"] = yyyymmdd(data["date"])
    for column in ("post_open", "post_high", "post_low", "post_close"):
        del data[column]
    return data


def load_mcp_json(pattern: str) -> pd.DataFrame:
    rows: list = []
    columns = None
    for path in sorted(Path().glob(pattern)):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("truncated") or len(payload.get("rows", [])) != int(payload.get("total_rows", -1)):
            raise DataContractError(f"incomplete MCP artifact: {path}")
        artifact_columns = payload.get("columns")
        if artifact_columns is not None:
            columns = columns or artifact_columns
            if columns != artifact_columns:
                raise DataContractError(f"MCP artifact schema mismatch: {path}")
        rows.extend(payload["rows"])
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=columns) if columns else pd.DataFrame.from_records(rows)


def build_fund_daily() -> pd.DataFrame:
    data = load_mcp_json("data/mcp_raw/fund_daily_*.json")
    if data.empty:
        raise DataContractError("no complete ETF MCP artifacts found")
    data = data.sort_values(["symbol", "date"]).drop_duplicates(["date", "symbol"], keep="last")
    factor = pd.to_numeric(data["cum_adj_factor"], errors="coerce")
    latest = data.groupby("symbol")["cum_adj_factor"].transform("last")
    relative = factor / latest.replace(0, np.nan)
    for column in ("open", "high", "low", "close"):
        data[f"adj_{column}"] = pd.to_numeric(data[column], errors="coerce") * relative
    data["adj_pre_close"] = data.groupby("symbol", sort=False)["adj_close"].shift(1)
    first = data["adj_pre_close"].isna()
    data.loc[first, "adj_pre_close"] = pd.to_numeric(data.loc[first, "pre_close"], errors="coerce") * relative[first]
    data["date"] = yyyymmdd(data["date"])
    return data


def quarter_end(value: pd.Series) -> pd.Series:
    periods = value.astype(str).str.lower().str.replace("q", "Q", regex=False)
    return pd.PeriodIndex(periods, freq="Q").end_time.normalize()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="PandaData-backed QuantBT lake root")
    parser.add_argument("--config", default="config/default.yaml")
    args = parser.parse_args()
    cfg = load_config(args.config)
    source = Path(args.source)
    cache = Path(cfg["data"]["cache_dir"])
    cache.mkdir(parents=True, exist_ok=True)
    start = int(str(cfg["run"]["start_date"]).replace("-", ""))
    end = int(str(cfg["run"]["effective_end_date"]).replace("-", ""))

    stock_path = cache / "stock_daily.parquet"
    if not stock_path.is_file():
        raw = read_dataset(source, "bars", start, end)
        post = read_dataset(source, "bars_post_adjusted", start, end)
        stock = normalized_prices(raw, post)
        stock.to_parquet(stock_path, index=False, compression="zstd")
        del raw, post, stock

    index = read_dataset(source, "index_bars", start, end)
    index = index[index["symbol"] == cfg["run"]["benchmark"]].drop_duplicates(["date", "symbol"], keep="last")
    index["date"] = yyyymmdd(index["date"])
    index.to_parquet(cache / "index_daily.parquet", index=False, compression="zstd")

    fund = build_fund_daily()
    fund.to_parquet(cache / "fund_daily.parquet", index=False, compression="zstd")

    reports = read_dataset(source, "financial_reports")
    reports = reports[pd.to_numeric(reports["date"], errors="coerce") <= end]
    reports["announcement_date"] = yyyymmdd(reports["date"])
    reports["report_period"] = quarter_end(reports["quarter"])
    reports["revenue"] = reports["operating_revenue"]
    reports["net_profit"] = reports["np_parent_owners"]
    reports["bvps"] = reports["se_parent_owners"] / reports["total_shares"].replace(0, np.nan)
    computed_roe = reports["np_parent_owners"] / reports["se_parent_owners"].replace(0, np.nan) * 100
    reports["roe"] = reports["roe"].fillna(computed_roe)
    fundamentals = reports[["symbol", "announcement_date", "report_period", "revenue", "roe", "total_assets",
                            "total_shares", "bvps", "net_profit"]].drop_duplicates(
        ["symbol", "announcement_date", "report_period"], keep="last"
    )
    fundamentals.to_parquet(cache / "fundamentals.parquet", index=False, compression="zstd")

    dividends = read_dataset(source, "cash_dividends")
    dividends = dividends[pd.to_numeric(dividends["announcement_date"], errors="coerce") <= end]
    dividends["effective_date"] = dividends.get("record_date")
    dividends["div_type"] = "cash"
    for column in ("announcement_date", "effective_date", "ex_date"):
        dividends[column] = yyyymmdd(dividends[column])
    dividends[["symbol", "announcement_date", "effective_date", "ex_date", "div_type"]].to_parquet(
        cache / "dividend.parquet", index=False, compression="zstd"
    )

    details = read_dataset(source, "stock_details")
    details = details.rename(columns={"listed_date": "list_date", "de_listed_date": "delist_date", "board_type": "board"})
    details.loc[pd.to_numeric(details["delist_date"], errors="coerce").fillna(0) == 0, "delist_date"] = None
    details["list_date"] = yyyymmdd(details["list_date"])
    details["delist_date"] = yyyymmdd(details["delist_date"])
    details = details[details["list_date"].notna()].copy()
    details[["symbol", "name", "list_date", "delist_date", "board"]].to_parquet(
        cache / "security_master.parquet", index=False, compression="zstd"
    )

    audit = load_mcp_json("data/mcp_raw/audit_*.json")
    if audit.empty:
        raise DataContractError("audit MCP artifacts are still required")
    audit["audit_proxy"] = False
    audit["date"] = yyyymmdd(audit["date"])
    historical = reports[reports["quarter"].astype(str).str.endswith("q4")].copy()
    historical = historical[quarter_end(historical["quarter"]) < pd.Timestamp("2023-01-01")]
    proxy = historical[["symbol", "quarter", "announcement_date"]].drop_duplicates(["symbol", "quarter"], keep="last")
    proxy = proxy.rename(columns={"announcement_date": "date"})
    proxy["agency"] = None
    proxy["audit_type"] = "financial_statements"
    proxy["opinion"] = "annual_report_available_proxy"
    proxy["audit_proxy"] = True
    audit = pd.concat([audit, proxy], ignore_index=True).sort_values(
        ["symbol", "quarter", "audit_proxy"]
    ).drop_duplicates(["symbol", "quarter", "audit_type"], keep="first")
    audit.to_parquet(cache / "audit_opinion.parquet", index=False, compression="zstd")

    artifacts = []
    for path in sorted(cache.glob("*.parquet")):
        artifacts.append({"table": path.stem, "local_path": str(path.resolve()), "total_rows": len(pd.read_parquet(path, columns=[])),
                          "sha256": sha256_file(path), "truncated": False})
    # Correct row counts without loading the tables again.
    import pyarrow.parquet as pq
    for item in artifacts:
        item["total_rows"] = pq.ParquetFile(item["local_path"]).metadata.num_rows
    (cache / "manifest.json").write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source.resolve()), "source_kind": "existing Pandadata-backed QuantBT lake plus authenticated Pandadata MCP",
        "artifacts": artifacts}, ensure_ascii=False, indent=2), encoding="utf-8")
    findings = validate_cache(cache)
    Path(cfg["data"]["output_dir"]).joinpath("data_sync").mkdir(parents=True, exist_ok=True)
    Path(cfg["data"]["output_dir"]).joinpath("data_sync", "data_dictionary.json").write_text(
        json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not findings["ok"]:
        raise DataContractError("canonical cache failed validation")
    print(json.dumps({"ok": True, "tables": {key: value.get("rows") for key, value in findings["tables"].items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
