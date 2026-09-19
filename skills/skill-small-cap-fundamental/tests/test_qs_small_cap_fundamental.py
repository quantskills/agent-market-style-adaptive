import numpy as np
import pandas as pd
import pytest
from qs_small_cap_fundamental import average_true_range, holding_count_from_deviation, select_small_cap, small_cap_stop
from qs_small_cap_fundamental.strategy import UNQUALIFIED_OPINIONS
from agent_market_style_adaptive.config import load_config

def test_atr_includes_overnight_gap():
    h = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=15), "close": [100.] * 14 + [110.], "high": [101.] * 14 + [111.], "low": [99.] * 14 + [109.]})
    assert average_true_range(h) == pytest.approx((13 * 2 + 11) / 14)

def test_audit_proxy_is_not_an_opinion():
    assert "annual_report_available_proxy" not in UNQUALIFIED_OPINIONS

def test_fixed_stop_survives_large_atr():
    cfg = {"fixed_stop": .09, "protect_trigger": .08, "protect_floor": .01, "atr_multiple": 3}
    hit, price, reason = small_cap_stop(100, 100, 90, 20, cfg)
    assert hit and price == pytest.approx(91) and reason == "fixed"


def test_dividend_filter_can_be_relaxed_but_audit_cannot():
    cfg = load_config("config/default.yaml")["small_cap"]
    cfg["require_recent_dividend"] = False
    snapshot = pd.DataFrame({"symbol": ["000001.SZ"], "close": [10.0], "trade_status": [0]})
    fundamentals = pd.DataFrame({
        "symbol": ["000001.SZ"], "announcement_date": ["2024-04-01"],
        "report_period": ["2023-12-31"], "revenue": [2e9], "roe": [.12],
        "total_assets": [1e10], "total_shares": [5e8], "bvps": [5.0],
        "net_profit": [5e8],
    })
    audit = pd.DataFrame({
        "symbol": ["000001.SZ"], "date": ["2024-04-01"], "quarter": ["2023Q4"],
        "audit_type": ["financial_statements"], "opinion": ["unqualified_opinion"],
    })
    index_history = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=10), "close": 100.0})
    selected = select_small_cap(
        "2024-05-01", snapshot, fundamentals, audit, pd.DataFrame(), index_history, cfg
    )
    assert selected.symbols == ["000001.SZ"]
    assert bool(selected.evidence.iloc[0]["audit_ok"])
    without_audit = select_small_cap(
        "2024-05-01", snapshot, fundamentals, pd.DataFrame(), pd.DataFrame(), index_history, cfg
    )
    assert without_audit.symbols == []


def test_strict_small_cap_fundamental_thresholds_and_dividend_gate():
    cfg = load_config("config/optimized.rebalance.yaml")["small_cap"]
    symbols = ["PASS.SZ", "LOWPROFIT.SZ", "LOWREV.SZ", "TOOLARGE.SZ"]
    snapshot = pd.DataFrame({
        "symbol": symbols,
        "close": [10.0, 10.0, 10.0, 10.0],
        "trade_status": [0, 0, 0, 0],
    })
    fundamentals = pd.DataFrame({
        "symbol": symbols,
        "announcement_date": ["2024-04-01"] * 4,
        "report_period": ["2023-12-31"] * 4,
        "revenue": [2e8, 2e8, 5e7, 2e8],
        "roe": [.12] * 4,
        "total_assets": [1e8] * 4,
        "total_shares": [5e8, 5e8, 5e8, 2e9],
        "bvps": [5.0] * 4,
        "net_profit": [3e6, 1e6, 3e6, 3e6],
    })
    audit = pd.DataFrame({
        "symbol": symbols,
        "date": ["2024-04-01"] * 4,
        "quarter": ["2023Q4"] * 4,
        "audit_type": ["financial_statements"] * 4,
        "opinion": ["unqualified_opinion"] * 4,
    })
    dividends = pd.DataFrame({
        "symbol": ["PASS.SZ"], "announcement_date": ["2024-03-01"],
        "effective_date": ["2024-05-01"], "ex_date": ["2024-05-01"],
        "div_type": ["cash"],
    })
    index_history = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=10), "close": 100.0})
    selected = select_small_cap(
        "2024-05-15", snapshot, fundamentals, audit, dividends, index_history, cfg
    )
    assert selected.symbols == ["PASS.SZ"]
    evidence = selected.evidence.set_index("symbol")
    assert not bool(evidence.loc["LOWPROFIT.SZ", "fundamental_ok"])
    assert not bool(evidence.loc["LOWREV.SZ", "fundamental_ok"])
    assert not bool(evidence.loc["TOOLARGE.SZ", "fundamental_ok"])
    assert bool(evidence.loc["PASS.SZ", "audit_ok"])
    assert bool(evidence.loc["PASS.SZ", "dividend_ok"])


def test_joinquant_style_price_board_and_calendar_filters():
    cfg = load_config("config/optimized.rebalance.yaml")["small_cap"]
    snapshot = pd.DataFrame({
        "symbol": ["600001.SH", "300001.SZ", "600002.SH"],
        "close": [10.0, 10.0, 25.0], "trade_status": [0, 0, 0],
        "limit_up": [11.0, 11.0, 27.5], "limit_down": [9.0, 9.0, 22.5],
    })
    fundamentals = pd.DataFrame({
        "symbol": ["600001.SH", "300001.SZ", "600002.SH"],
        "announcement_date": ["2024-04-01"] * 3, "report_period": ["2023-12-31"] * 3,
        "revenue": [2e8] * 3, "roe": [.1] * 3, "total_assets": [1e8] * 3,
        "total_shares": [5e8] * 3, "bvps": [5.0] * 3, "net_profit": [3e6] * 3,
    })
    audit = pd.DataFrame({"symbol": ["600001.SH", "300001.SZ", "600002.SH"],
        "date": ["2024-04-01"] * 3, "quarter": ["2023Q4"] * 3,
        "audit_type": ["financial_statements"] * 3, "opinion": ["unqualified_opinion"] * 3})
    dividends = pd.DataFrame({"symbol": ["600001.SH", "300001.SZ", "600002.SH"],
        "announcement_date": ["2024-03-01"] * 3, "effective_date": ["2024-05-01"] * 3,
        "ex_date": ["2024-05-01"] * 3, "div_type": ["cash"] * 3})
    index_history = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=10), "close": 100.0})
    selection = select_small_cap("2024-05-15", snapshot, fundamentals, audit, dividends, index_history, cfg)
    assert selection.symbols == ["600001.SH"]
    skipped = select_small_cap("2024-04-15", snapshot, fundamentals, audit, dividends, index_history, cfg)
    assert skipped.symbols == []
