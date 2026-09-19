from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import pandas.testing as pdt

from agent_market_style_adaptive.backtest import Backtester
from agent_market_style_adaptive.config import load_config
from agent_market_style_adaptive.reporting import build_report


def synthetic_case():
    config = load_config(Path(__file__).parents[1] / "config" / "default.yaml")
    dates = pd.bdate_range("2023-01-02", periods=280)
    config["run"].update({"start_date": str(dates[130].date()), "effective_end_date": str(dates[-1].date()), "end_date": str(dates[-1].date())})
    index = pd.DataFrame({"date": dates, "symbol": "000300.SH", "open": np.linspace(100, 130, len(dates)), "close": np.linspace(100, 130, len(dates)), "high": np.linspace(101, 131, len(dates)), "low": np.linspace(99, 129, len(dates))})
    stock_parts = []
    fundamentals = []
    audits = []
    dividends = []
    master = []
    for number, symbol in enumerate(["000001.SZ", "000002.SZ", "000003.SZ"]):
        price = np.linspace(10 + number, 15 + number, len(dates))
        raw = pd.DataFrame({"date": dates, "symbol": symbol, "name": f"Company {number}", "open": price, "high": price * 1.01, "low": price * .99, "close": price,
                            "pre_close": np.r_[price[0], price[:-1]], "adj_open": price, "adj_high": price * 1.01, "adj_low": price * .99, "adj_close": price,
                            "adj_pre_close": np.r_[price[0], price[:-1]], "amount": np.linspace(1e7, 2e7, len(dates)), "limit_up": price * 1.1,
                            "limit_down": price * .9, "trade_status": 0})
        stock_parts.append(raw)
        fundamentals.append({"symbol": symbol, "announcement_date": dates[20], "report_period": dates[10], "revenue": 2e9, "roe": 12,
                             "total_assets": 4e9, "total_shares": 2e8 + number * 1e7, "bvps": 8, "net_profit": 2e8})
        audits.append({"symbol": symbol, "date": dates[20], "quarter": "2022q4", "audit_type": "financial_statements", "opinion": "unqualified_opinion"})
        dividends.append({"symbol": symbol, "announcement_date": dates[30], "effective_date": dates[40], "ex_date": dates[40], "div_type": "cash"})
        master.append({"symbol": symbol, "name": f"Company {number}", "list_date": dates[0], "delist_date": pd.NaT, "board": "MainBoard"})
    fund_parts = []
    for number, symbol in enumerate(config["etf"]["symbols"]):
        price = np.linspace(1 + number / 10, 2 + number / 10, len(dates))
        fund_parts.append(pd.DataFrame({"date": dates, "symbol": symbol, "open": price, "high": price * 1.01, "low": price * .99, "close": price,
                                        "pre_close": np.r_[price[0], price[:-1]], "adj_open": price, "adj_high": price * 1.01, "adj_low": price * .99,
                                        "adj_close": price, "adj_pre_close": np.r_[price[0], price[:-1]], "amount": np.linspace(1e7, 3e7, len(dates)),
                                        "limit_up": price * 1.1, "limit_down": price * .9}))
    tables = {"stock_daily": pd.concat(stock_parts, ignore_index=True), "fund_daily": pd.concat(fund_parts, ignore_index=True), "index_daily": index,
              "fundamentals": pd.DataFrame(fundamentals), "audit_opinion": pd.DataFrame(audits), "dividend": pd.DataFrame(dividends), "security_master": pd.DataFrame(master)}
    return config, tables


def test_same_inputs_produce_identical_ledger_and_nav():
    config, tables = synthetic_case()
    first = Backtester(deepcopy(config), deepcopy(tables)).run("adaptive")
    second = Backtester(deepcopy(config), deepcopy(tables)).run("adaptive")
    pdt.assert_frame_equal(first.equity, second.equity)
    pdt.assert_frame_equal(first.trades, second.trades)
    if not first.trades.empty:
        first_signal = first.evidence[first.evidence["event"] == "selection"]["date"].min()
        assert first.trades["date"].min() > first_signal


def test_backtest_outputs_feed_comparison_report(tmp_path):
    config, tables = synthetic_case()
    for mode in ("adaptive",):
        Backtester(deepcopy(config), deepcopy(tables)).run(mode).write(tmp_path)
    report = build_report(tmp_path)
    assert report.is_file()
    assert (tmp_path / "comparison.csv").is_file()
    assert "参考陈述" in report.read_text(encoding="utf-8")

