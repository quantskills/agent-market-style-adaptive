from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq


TABLE_CONTRACTS: dict[str, set[str]] = {
    "stock_daily": {"date", "symbol", "name", "open", "high", "low", "close", "pre_close", "adj_open", "adj_high", "adj_low", "adj_close", "adj_pre_close", "amount", "limit_up", "limit_down", "trade_status"},
    "fund_daily": {"date", "symbol", "open", "high", "low", "close", "pre_close", "adj_open", "adj_high", "adj_low", "adj_close", "adj_pre_close", "amount", "limit_up", "limit_down"},
    "index_daily": {"date", "symbol", "open", "high", "low", "close"},
    "fundamentals": {"symbol", "announcement_date", "report_period", "revenue", "roe", "total_assets", "total_shares", "bvps", "net_profit"},
    "audit_opinion": {"symbol", "date", "quarter", "audit_type", "opinion"},
    "dividend": {"symbol", "announcement_date", "effective_date", "ex_date", "div_type"},
    "security_master": {"symbol", "name", "list_date", "delist_date", "board"},
}

DATE_COLUMNS = {"date", "announcement_date", "report_period", "effective_date", "ex_date", "list_date", "delist_date"}


class DataContractError(RuntimeError):
    pass


def build_mcp_requests(config: dict) -> list[dict[str, Any]]:
    start = pd.Timestamp(config["run"]["start_date"])
    end = pd.Timestamp(config["run"]["effective_end_date"])
    chunks = _date_chunks(start, end, years=4)
    etfs = config["etf"]["symbols"]
    requests: list[dict[str, Any]] = [
        _request("_preflight", "preflight_small", "get_fund_daily", {"start_date": "20260529", "end_date": "20260529", "symbol": etfs}),
        _request("_preflight", "preflight_large", "get_stock_daily", {"start_date": "20260101", "end_date": "20260331"}),
    ]
    for number, (left, right) in enumerate(chunks, 1):
        params = {"start_date": left.strftime("%Y%m%d"), "end_date": right.strftime("%Y%m%d")}
        requests.append(_request("stock_daily", f"stock_daily_{number}", "get_stock_daily", params))
        requests.append(_request("stock_daily", f"stock_daily_pre_{number}", "get_stock_daily_pre", params, transform="merge_adjusted_ohlc"))
        requests.append(_request("index_daily", f"index_daily_{number}", "get_index_daily", params | {"symbol": config["run"]["benchmark"]}))
    requests.extend([
        _request("fund_daily", "fund_daily", "get_fund_daily", {"start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"), "symbol": etfs}),
        _request("fund_daily", "fund_daily_pre", "get_fund_daily_pre", {"start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"), "symbol": etfs}, transform="merge_adjusted_ohlc"),
        _request("audit_opinion", "audit_opinion", "get_audit_opinion", {"start_quarter": "2015q1", "end_quarter": "2026q2", "market": "cn"}),
        _request("dividend", "dividend", "get_stock_dividend", {"start_date": "20130601", "end_date": end.strftime("%Y%m%d")}),
        _request("security_master", "security_master", "get_stock_detail", {"status": None}, transform="normalize_security_master"),
    ])
    financial_fields = ["symbol", "info_date", "end_date", "operating_revenue", "net_profit_parent", "total_assets", "total_shares", "roe_diluted", "bvps"]
    for quarter in _quarters(2015, 1, 2026, 2):
        requests.append(_request("fundamentals", f"fundamentals_{quarter}", "get_fina_performance",
                                 {"end_quarter": quarter, "fields": financial_fields}, transform="normalize_fundamentals"))
    return requests


def _request(table: str, request_id: str, method: str, params: dict, transform: str | None = None) -> dict:
    return {"id": request_id, "table": table, "method": method, "params": params, "transform": transform,
            "requirements": {"untruncated": True, "local_parquet": True}}


def _date_chunks(start: pd.Timestamp, end: pd.Timestamp, years: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    output = []
    cursor = start
    while cursor <= end:
        right = min(cursor + pd.DateOffset(years=years) - pd.Timedelta(days=1), end)
        output.append((cursor, right))
        cursor = right + pd.Timedelta(days=1)
    return output


def _quarters(start_year: int, start_quarter: int, end_year: int, end_quarter: int) -> list[str]:
    return [f"{year}q{quarter}" for year in range(start_year, end_year + 1) for quarter in range(1, 5)
            if (year, quarter) >= (start_year, start_quarter) and (year, quarter) <= (end_year, end_quarter)]


def write_request_plan(config: dict, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "transport": "Pandadata MCP only",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "requirements": {"local_artifact": True, "untruncated": True, "format": "parquet"},
        "requests": build_mcp_requests(config),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_mcp_command(config: dict, request_plan: str | Path) -> Path:
    """Invoke an MCP host bridge. It must materialize artifacts and return a manifest path.

    The command receives the request-plan JSON on stdin. Its stdout must be a single
    JSON object with `manifest_path`. This intentionally has no SDK fallback.
    """
    command = config["data"].get("mcp_command") or os.environ.get("PANDADATA_MCP_COMMAND")
    if not command:
        raise DataContractError(
            "Pandadata MCP bridge is not configured. Execute the generated request plan with the host MCP, "
            "materialize every Parquet artifact, then run data-sync --validate-only."
        )
    payload = Path(request_plan).read_text(encoding="utf-8")
    completed = subprocess.run(shlex.split(command), input=payload, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise DataContractError(f"Pandadata MCP bridge failed: {completed.stderr.strip()}")
    try:
        response = json.loads(completed.stdout)
        manifest = Path(response["manifest_path"])
    except (json.JSONDecodeError, KeyError) as exc:
        raise DataContractError("MCP bridge did not return JSON with manifest_path") from exc
    validate_raw_manifest(manifest)
    return manifest


def validate_raw_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise DataContractError(f"raw manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = payload.get("artifacts", [])
    if not artifacts:
        raise DataContractError("raw manifest has no artifacts")
    for item in artifacts:
        artifact = Path(item.get("local_path", ""))
        if not artifact.is_file() or artifact.suffix.lower() != ".parquet":
            raise DataContractError(f"MCP artifact was not materialized locally as Parquet: {artifact}")
        if item.get("sha256") and sha256_file(artifact) != item["sha256"]:
            raise DataContractError(f"raw artifact hash mismatch: {artifact}")
        expected_rows = item.get("total_rows")
        if expected_rows is not None and pq.ParquetFile(artifact).metadata.num_rows != int(expected_rows):
            raise DataContractError(f"raw artifact row count does not match full MCP result: {artifact}")
    return payload


def materialize_raw_artifacts(config: dict, request_plan: str | Path, raw_manifest: str | Path) -> Path:
    """Normalize locally downloaded MCP Parquet artifacts into canonical cache tables."""
    plan = json.loads(Path(request_plan).read_text(encoding="utf-8"))
    raw = validate_raw_manifest(raw_manifest)
    requests = {item["id"]: item for item in plan["requests"]}
    artifacts = {item.get("request_id", item.get("id")): item for item in raw["artifacts"]}
    missing = sorted(set(requests) - set(artifacts))
    if missing:
        raise DataContractError(f"raw manifest is missing MCP requests: {', '.join(missing)}")
    grouped: dict[str, list[tuple[dict, pd.DataFrame]]] = {}
    for request_id, request in requests.items():
        if request["table"] == "_preflight":
            continue
        frame = pd.read_parquet(artifacts[request_id]["local_path"])
        grouped.setdefault(request["table"], []).append((request, frame))

    normalized: dict[str, pd.DataFrame] = {}
    for table, pieces in grouped.items():
        raw_parts = [frame for request, frame in pieces if request.get("transform") != "merge_adjusted_ohlc"]
        adjusted_parts = [frame for request, frame in pieces if request.get("transform") == "merge_adjusted_ohlc"]
        base = pd.concat(raw_parts, ignore_index=True) if raw_parts else pd.DataFrame()
        if adjusted_parts:
            adjusted = pd.concat(adjusted_parts, ignore_index=True)
            rename = {column: f"adj_{column}" for column in ("open", "high", "low", "close", "pre_close") if column in adjusted.columns}
            adjusted = adjusted.rename(columns=rename)
            keep = [column for column in ("date", "symbol", "adj_open", "adj_high", "adj_low", "adj_close", "adj_pre_close") if column in adjusted]
            base = base.merge(adjusted[keep].drop_duplicates(["date", "symbol"], keep="last"), on=["date", "symbol"], how="inner", validate="one_to_one")
        if table == "fundamentals":
            base = base.rename(columns={"info_date": "announcement_date", "end_date": "report_period", "operating_revenue": "revenue",
                                        "net_profit_parent": "net_profit", "roe_diluted": "roe"})
        elif table == "security_master":
            base = base.rename(columns={"listed_date": "list_date", "de_listed_date": "delist_date", "board_type": "board"})
        key = [column for column in ("date", "announcement_date", "symbol", "report_period", "quarter") if column in base.columns]
        normalized[table] = base.sort_values(key).drop_duplicates(key, keep="last") if key else base

    cache = Path(config["data"]["cache_dir"])
    cache.mkdir(parents=True, exist_ok=True)
    final_artifacts = []
    for table in TABLE_CONTRACTS:
        if table not in normalized:
            raise DataContractError(f"no normalized data produced for required table: {table}")
        path = (cache / f"{table}.parquet").resolve()
        normalized[table].to_parquet(path, index=False)
        final_artifacts.append({"table": table, "local_path": str(path), "total_rows": len(normalized[table]), "sha256": sha256_file(path), "truncated": False})
    manifest = cache / "manifest.json"
    manifest.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "source_manifest": str(Path(raw_manifest).resolve()),
                                    "artifacts": final_artifacts}, ensure_ascii=False, indent=2), encoding="utf-8")
    validate_manifest(manifest, cache)
    findings = validate_cache(cache)
    if not findings["ok"]:
        raise DataContractError("normalized cache failed its data contract; inspect outputs/data_sync/data_dictionary.json")
    config_payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    receipt = {
        "source": "pandadata",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest.resolve()),
        "manifest_created_at": json.loads(manifest.read_text(encoding="utf-8")).get("created_at"),
        "config_hash": hashlib.sha256(config_payload.encode("utf-8")).hexdigest(),
        "request_plan": str(Path(request_plan).resolve()),
    }
    (cache / "sync_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def require_fresh_sync(config: dict) -> None:
    """Reject stale/local-only caches when the configuration requires PandaData."""
    if not config.get("data", {}).get("require_fresh_sync", False):
        return
    cache = Path(config["data"]["cache_dir"])
    receipt_path = cache / "sync_receipt.json"
    manifest_path = cache / "manifest.json"
    if not receipt_path.is_file() or not manifest_path.is_file():
        raise DataContractError("fresh PandaData sync required; run `market-style-agent data-sync --execute` with your PANDADATA_MCP_COMMAND before backtest/compare/report")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataContractError("PandaData sync receipt or manifest is unreadable; run data-sync --execute again") from exc
    if receipt.get("source") != "pandadata" or Path(receipt.get("manifest", "")).resolve() != manifest_path.resolve():
        raise DataContractError("cache provenance is not a valid PandaData sync; run data-sync --execute again")
    expected = hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if receipt.get("config_hash") != expected:
        raise DataContractError("PandaData cache was produced for a different configuration; run data-sync --execute again")
    if receipt.get("manifest_created_at") != manifest.get("created_at"):
        raise DataContractError("PandaData sync receipt does not match manifest; run data-sync --execute again")


def validate_cache(cache_dir: str | Path, required_tables: list[str] | None = None) -> dict[str, Any]:
    root = Path(cache_dir)
    tables = required_tables or list(TABLE_CONTRACTS)
    findings: dict[str, Any] = {"ok": True, "tables": {}, "warnings": []}
    for table in tables:
        path = root / f"{table}.parquet"
        item: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
        if not path.is_file():
            item["errors"] = ["missing file"]
            findings["ok"] = False
            findings["tables"][table] = item
            continue
        frame = pd.read_parquet(path)
        missing = sorted(TABLE_CONTRACTS[table] - set(frame.columns))
        duplicates = int(frame.duplicated([col for col in ("date", "symbol") if col in frame.columns]).sum())
        item.update({"rows": len(frame), "columns": list(frame.columns), "missing_columns": missing, "duplicate_keys": duplicates, "sha256": sha256_file(path)})
        errors = []
        if missing:
            errors.append(f"missing columns: {', '.join(missing)}")
        if frame.empty:
            errors.append("empty table")
        if duplicates and table in {"stock_daily", "fund_daily", "index_daily"}:
            errors.append(f"duplicate date/symbol keys: {duplicates}")
        for column in DATE_COLUMNS.intersection(frame.columns):
            invalid_dates = int(pd.to_datetime(frame[column], errors="coerce").isna().sum())
            if invalid_dates and column not in {"delist_date", "effective_date", "ex_date"}:
                errors.append(f"invalid or missing {column}: {invalid_dates}")
        if table in {"stock_daily", "fund_daily", "index_daily"} and not frame.empty:
            dates = pd.to_datetime(frame["date"], errors="coerce")
            item["date_min"], item["date_max"] = str(dates.min().date()), str(dates.max().date())
            nonpositive = int((pd.to_numeric(frame["close"], errors="coerce") <= 0).sum())
            if nonpositive:
                errors.append(f"nonpositive close values: {nonpositive}")
            price_column = "adj_close" if "adj_close" in frame.columns else "close"
            jumps = frame.sort_values(["symbol", "date"]).groupby("symbol")[price_column].pct_change(fill_method=None).abs()
            item["adjusted_jumps_over_50pct"] = int((jumps > 0.50).sum())
            if item["adjusted_jumps_over_50pct"]:
                findings["warnings"].append(f"{table}: inspect {item['adjusted_jumps_over_50pct']} adjusted jumps above 50%")
        if table == "fundamentals" and not frame.empty:
            announced = pd.to_datetime(frame["announcement_date"], errors="coerce")
            period = pd.to_datetime(frame["report_period"], errors="coerce")
            future_leaks = int((announced < period).sum())
            item["announcement_before_period_end"] = future_leaks
            if future_leaks:
                errors.append(f"announcement precedes report period: {future_leaks}")
        if table == "security_master" and not frame.empty:
            delisted = pd.to_datetime(frame["delist_date"], errors="coerce").notna()
            item["delisted_symbols"] = int(delisted.sum())
            if not delisted.any():
                findings["warnings"].append("security_master has no delisted symbols; survivorship coverage needs review")
        item["errors"] = errors
        findings["ok"] &= not errors
        findings["tables"][table] = item
    return findings


def validate_manifest(path: str | Path, cache_dir: str | Path) -> None:
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise DataContractError(f"manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = payload.get("artifacts", [])
    if not artifacts:
        raise DataContractError("manifest has no artifacts")
    cache_root = Path(cache_dir).resolve()
    for item in artifacts:
        artifact = Path(item.get("local_path", "")).resolve()
        if not artifact.is_file() or artifact.suffix.lower() != ".parquet":
            raise DataContractError(f"MCP artifact was not materialized as Parquet: {artifact}")
        if cache_root not in artifact.parents and artifact != cache_root:
            raise DataContractError(f"artifact is outside configured cache: {artifact}")
        if item.get("sha256") and sha256_file(artifact) != item["sha256"]:
            raise DataContractError(f"artifact hash mismatch: {artifact}")
        expected_rows = item.get("total_rows")
        if expected_rows is not None and pq.ParquetFile(artifact).metadata.num_rows != int(expected_rows):
            raise DataContractError(f"artifact row count mismatch: {artifact}")


def load_tables(cache_dir: str | Path) -> dict[str, pd.DataFrame]:
    root = Path(cache_dir)
    tables = {name: pd.read_parquet(root / f"{name}.parquet") for name in TABLE_CONTRACTS}
    for frame in tables.values():
        for column in DATE_COLUMNS.intersection(frame.columns):
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return tables


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
