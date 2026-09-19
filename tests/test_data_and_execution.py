from pathlib import Path

import pandas as pd

from agent_market_style_adaptive.backtest import Backtester
import json

from agent_market_style_adaptive.data import build_mcp_requests, materialize_raw_artifacts, sha256_file, validate_cache
from agent_market_style_adaptive.config import load_config


CONFIG = load_config(Path(__file__).parents[1] / "config" / "default.yaml")


def test_mcp_plan_chunks_stock_history_below_five_years():
    requests = [item for item in build_mcp_requests(CONFIG) if item["method"] == "get_stock_daily" and item["table"] == "stock_daily"]
    assert len(requests) == 3
    for item in requests:
        start, end = pd.Timestamp(item["params"]["start_date"]), pd.Timestamp(item["params"]["end_date"])
        assert end < start + pd.DateOffset(years=5)


def test_price_limit_and_suspension_checks():
    assert Backtester._blocked_reason("buy", pd.Series({"open": 10, "limit_up": 10, "limit_down": 8, "trade_status": 0})) == "limit_up"
    assert Backtester._blocked_reason("sell", pd.Series({"open": 8, "limit_up": 10, "limit_down": 8, "trade_status": 0})) == "limit_down"
    assert Backtester._blocked_reason("buy", pd.Series({"open": 9, "limit_up": 10, "limit_down": 8, "trade_status": 1})) == "suspended"


def test_cache_audit_detects_duplicate_keys(tmp_path):
    for table, columns in {
        "stock_daily": ["date", "symbol", "name", "open", "high", "low", "close", "pre_close", "adj_open", "adj_high", "adj_low", "adj_close", "adj_pre_close", "amount", "limit_up", "limit_down", "trade_status"],
    }.items():
        row = {column: 1 for column in columns}
        row.update({"date": "2024-01-01", "symbol": "000001.SZ"})
        pd.DataFrame([row, row]).to_parquet(tmp_path / f"{table}.parquet", index=False)
    result = validate_cache(tmp_path, ["stock_daily"])
    assert not result["ok"]
    assert result["tables"]["stock_daily"]["duplicate_keys"] == 1


def test_raw_mcp_artifacts_are_normalized_and_hashed(tmp_path):
    raw_dir, cache_dir = tmp_path / "raw", tmp_path / "cache"
    raw_dir.mkdir()
    config = load_config(Path(__file__).parents[1] / "config" / "default.yaml")
    config["data"]["cache_dir"] = str(cache_dir)
    requests = [
        {"id": "stock", "table": "stock_daily", "transform": None},
        {"id": "stock_adj", "table": "stock_daily", "transform": "merge_adjusted_ohlc"},
        {"id": "fund", "table": "fund_daily", "transform": None},
        {"id": "fund_adj", "table": "fund_daily", "transform": "merge_adjusted_ohlc"},
        {"id": "index", "table": "index_daily", "transform": None},
        {"id": "fina", "table": "fundamentals", "transform": "normalize_fundamentals"},
        {"id": "audit", "table": "audit_opinion", "transform": None},
        {"id": "div", "table": "dividend", "transform": None},
        {"id": "master", "table": "security_master", "transform": "normalize_security_master"},
    ]
    frames = {
        "stock": pd.DataFrame([{"date": "20240102", "symbol": "000001.SZ", "name": "A", "open": 10, "high": 11, "low": 9, "close": 10, "pre_close": 10, "amount": 1e6, "limit_up": 11, "limit_down": 9, "trade_status": 0}]),
        "stock_adj": pd.DataFrame([{"date": "20240102", "symbol": "000001.SZ", "open": 5, "high": 5.5, "low": 4.5, "close": 5, "pre_close": 5}]),
        "fund": pd.DataFrame([{"date": "20240102", "symbol": "518880.SH", "open": 4, "high": 4.1, "low": 3.9, "close": 4, "pre_close": 4, "amount": 1e6, "limit_up": 4.4, "limit_down": 3.6}]),
        "fund_adj": pd.DataFrame([{"date": "20240102", "symbol": "518880.SH", "open": 4, "high": 4.1, "low": 3.9, "close": 4, "pre_close": 4}]),
        "index": pd.DataFrame([{"date": "20240102", "symbol": "000300.SH", "open": 100, "high": 101, "low": 99, "close": 100}]),
        "fina": pd.DataFrame([{"symbol": "000001.SZ", "info_date": "20230401", "end_date": "20221231", "operating_revenue": 2e9, "net_profit_parent": 2e8, "total_assets": 4e9, "total_shares": 2e8, "roe_diluted": 10, "bvps": 8}]),
        "audit": pd.DataFrame([{"symbol": "000001.SZ", "date": "20230401", "quarter": "2022q4", "audit_type": "financial_statements", "opinion": "unqualified_opinion"}]),
        "div": pd.DataFrame([{"symbol": "000001.SZ", "announcement_date": "20230401", "effective_date": "20230601", "ex_date": "20230601", "div_type": "cash"}]),
        "master": pd.DataFrame([{"symbol": "000001.SZ", "name": "A", "listed_date": "19910101", "de_listed_date": None, "board_type": "MainBoard"}]),
    }
    artifacts = []
    for request_id, frame in frames.items():
        path = raw_dir / f"{request_id}.parquet"
        frame.to_parquet(path, index=False)
        artifacts.append({"request_id": request_id, "local_path": str(path), "total_rows": len(frame), "sha256": sha256_file(path)})
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"requests": requests}), encoding="utf-8")
    raw_manifest = tmp_path / "raw.json"
    raw_manifest.write_text(json.dumps({"artifacts": artifacts}), encoding="utf-8")
    manifest = materialize_raw_artifacts(config, plan, raw_manifest)
    assert manifest.is_file()
    stock = pd.read_parquet(cache_dir / "stock_daily.parquet")
    fundamentals = pd.read_parquet(cache_dir / "fundamentals.parquet")
    assert stock.loc[0, "adj_close"] == 5
    assert fundamentals.loc[0, "revenue"] == 2e9


def test_isolated_stop_preserves_unrelated_positions():
    from agent_market_style_adaptive.backtest import Position
    engine = object.__new__(Backtester)
    engine.config = CONFIG
    date = pd.Timestamp("2024-01-03")
    positions = {symbol: Position(symbol, "small_cap", 100, 10, date - pd.Timedelta(days=1), 10)
                 for symbol in ("000001.SZ", "000002.SZ")}
    day = pd.DataFrame([{ "symbol": s, "open": 10, "close": 10, "trade_status": 0 }
                        for s in positions]).set_index("symbol", drop=False)
    records = {"trades": [], "blocked": []}
    engine._execute(date, 0, positions, None, {"000001.SZ": "stop_fixed"}, day, pd.DataFrame(), records, 1)
    assert set(positions) == {"000002.SZ"}
    assert [t["symbol"] for t in records["trades"]] == ["000001.SZ"]


def test_blocked_stop_can_execute_on_subsequent_open():
    from agent_market_style_adaptive.backtest import Position
    engine = object.__new__(Backtester)
    engine.config = CONFIG
    date = pd.Timestamp("2024-01-03")
    positions = {"000001.SZ": Position("000001.SZ", "small_cap", 100, 10, date, 10)}
    day = pd.DataFrame([{ "symbol": "000001.SZ", "open": 10, "close": 10, "trade_status": 0 }]).set_index("symbol", drop=False)
    records = {"trades": [], "blocked": []}
    stops = {"000001.SZ": "stop_fixed"}
    cash = engine._execute(date, 0, positions, None, stops, day, pd.DataFrame(), records, 1)
    assert records["blocked"][0]["reason"] == "t_plus_one"
    cash = engine._execute(date + pd.Timedelta(days=1), cash, positions, None, stops, day, pd.DataFrame(), records, 1)
    assert not positions
    assert cash > 0


def test_fast_asof_keeps_original_announcement_date():
    source = pd.DataFrame({"symbol": ["A", "A", "B"],
                           "_source_asof_date": pd.to_datetime(["2024-01-01", "2024-01-03", "2024-01-05"]),
                           "value": [1, 2, 3]})
    result = Backtester._asof_snapshot(source, pd.Series(["A", "B"]), "announcement_date", pd.Timestamp("2024-01-03"))
    assert result.symbol.tolist() == ["A"]
    assert result.value.tolist() == [1]
    assert result.announcement_date.iloc[0] == pd.Timestamp("2024-01-01")
