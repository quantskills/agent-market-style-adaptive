from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agent_market_style_adaptive.config import load_config
from agent_market_style_adaptive.strategies import (
    holding_count_from_deviation,
    point_in_time_latest,
    select_blue_chips,
    select_etfs,
    etf_stop,
    small_cap_stop,
)


CONFIG = load_config(Path(__file__).parents[1] / "config" / "default.yaml")


def test_point_in_time_is_strictly_before_signal_date():
    frame = pd.DataFrame({"symbol": ["A", "A"], "announcement_date": ["2024-01-01", "2024-02-01"], "value": [1, 2]})
    result = point_in_time_latest(frame, "2024-02-01")
    assert result.iloc[0]["value"] == 1


@pytest.mark.parametrize("last,expected", [(106, 5), (94, 20), (100, 10)])
def test_dynamic_small_cap_breadth(last, expected):
    close = [100.0] * 9 + [last]
    count, _ = holding_count_from_deviation(pd.DataFrame({"date": pd.date_range("2024-01-01", periods=10), "close": close}), CONFIG["small_cap"])
    assert count == expected


def test_three_stop_rules():
    cfg = CONFIG["small_cap"]
    assert small_cap_stop(100, 100, 90, np.nan, cfg)[0]
    hit, stop, reason = small_cap_stop(100, 109, 100.5, np.nan, cfg)
    assert hit and stop == pytest.approx(101) and reason == "profit_protection"
    hit, stop, reason = small_cap_stop(100, 120, 110, 2, cfg)
    assert hit and stop == pytest.approx(114) and reason == "atr"


def test_etf_requires_real_120_day_history_and_ranks_score():
    dates = pd.bdate_range("2024-01-01", periods=130)
    good = pd.DataFrame({"date": dates, "symbol": "518880.SH", "close": np.linspace(1, 2, len(dates)), "adj_close": np.linspace(1, 2, len(dates)), "amount": np.linspace(100, 500, len(dates))})
    too_new = pd.DataFrame({"date": dates[-40:], "symbol": "513100.SH", "close": np.linspace(1, 2, 40), "adj_close": np.linspace(1, 2, 40), "amount": np.linspace(100, 500, 40)})
    selection = select_etfs(dates[-1], pd.concat([good, too_new]), CONFIG["etf"])
    assert selection.symbols == ["518880.SH"]
    assert not bool(selection.evidence.set_index("symbol").loc["513100.SH", "eligible"])


def test_etf_relative_strength_and_trailing_stop():
    dates = pd.bdate_range("2024-01-01", periods=130)
    strong = pd.DataFrame({"date": dates, "symbol": "A", "close": np.linspace(1, 2, len(dates)), "amount": np.linspace(100, 500, len(dates))})
    weak = pd.DataFrame({"date": dates, "symbol": "B", "close": np.linspace(1, 1.2, len(dates)), "amount": np.linspace(100, 500, len(dates))})
    cfg = dict(CONFIG["etf"], symbols=["A", "B"], relative_strength_min_percentile=.75)
    assert select_etfs(dates[-1], pd.concat([strong, weak]), cfg).symbols == ["A"]
    hit, price, reason = etf_stop(100, 120, 107, {"fixed_stop": .08, "trailing_stop": .10, "trailing_trigger": .05})
    assert hit and price == pytest.approx(108) and reason == "trailing"


def test_blue_chip_excludes_growth_boards_st_and_high_price():
    dates = pd.bdate_range("2024-01-01", periods=65)
    parts, fundamentals = [], []
    specs = [("000001.SZ", "Main", 20), ("300001.SZ", "Growth", 20), ("000002.SZ", "ST Risk", 20), ("000003.SZ", "High", 55)]
    for i, (symbol, name, level) in enumerate(specs):
        close = np.linspace(level, level * (1.01 + i / 100), len(dates))
        parts.append(pd.DataFrame({"date": dates, "symbol": symbol, "name": name, "close": close, "adj_close": close, "trade_status": 0}))
        fundamentals.append({"symbol": symbol, "announcement_date": dates[0] - pd.Timedelta(days=1), "report_period": "2023-09-30",
                             "revenue": 2e9, "roe": 10, "net_profit": 1e8, "bvps": 10, "total_assets": 1e9, "total_shares": 1e8})
    selection = select_blue_chips(dates[-1], pd.concat(parts), pd.DataFrame(fundamentals), "warm", CONFIG["blue_chip"])
    assert selection.symbols == ["000001.SZ"]


def test_blue_chip_crisis_uses_cold_breadth():
    date = pd.Timestamp("2024-06-30")
    symbols = [f"600{i:03d}.SH" for i in range(20)]
    stock = pd.DataFrame({
        "date": [date] * 20, "symbol": symbols, "name": symbols,
        "close": [10.0] * 20, "trade_status": [0] * 20,
        "volatility": [0.10 + i / 1000 for i in range(20)],
    })
    fundamentals = pd.DataFrame({
        "symbol": symbols, "announcement_date": [date - pd.Timedelta(days=1)] * 20,
        "bvps": [5.0] * 20, "revenue": [2e9] * 20,
        "net_profit": [1e8] * 20, "roe": [10.0] * 20,
    })
    cfg = {"price_max": 50, "pb_min": 0, "pb_max": 3, "revenue_min": 1e9,
           "volatility_window": 60, "low_vol_quantile": 1.0,
           "holdings": {"cold": 15, "warm": 10, "overheated": 5}}
    result = select_blue_chips(date, stock, fundamentals, "crisis", cfg)
    assert len(result.symbols) == 15
    cfg["allowed_regimes"] = ["warm", "overheated"]
    assert select_blue_chips(date, stock, fundamentals, "crisis", cfg).symbols == []
