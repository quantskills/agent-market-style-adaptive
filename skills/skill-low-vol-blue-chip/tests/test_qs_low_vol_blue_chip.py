import pandas as pd
from qs_low_vol_blue_chip import select_blue_chips

def test_low_pb_breaks_equal_quality_and_volatility():
    date = pd.Timestamp("2024-06-28")
    snapshot = pd.DataFrame({"date": [date,date], "symbol": ["600001.SH","600002.SH"], "close": [10.,10.], "name": ["A","B"], "trade_status": [0,0], "volatility": [.1,.1]})
    financials = pd.DataFrame({"symbol": ["600001.SH","600002.SH"], "announcement_date": ["2024-04-01"] * 2, "bvps": [10.,5.], "revenue": [2e9]*2, "net_profit": [1e8]*2, "roe": [10.]*2})
    cfg = {"price_max":50,"pb_min":0,"pb_max":3,"revenue_min":1e9,"low_vol_quantile":.3,"volatility_window":60,"holdings":{"cold":1,"warm":1,"overheated":1}}
    selection = select_blue_chips(date, snapshot, financials, "warm", cfg)
    assert selection.symbols == ["600001.SH"]


def test_optional_quality_and_size_filters_use_point_in_time_fields():
    date = pd.Timestamp("2024-06-28")
    snapshot = pd.DataFrame({
        "date": [date, date], "symbol": ["600001.SH", "600002.SH"],
        "close": [10.0, 10.0], "name": ["A", "B"], "trade_status": [0, 0],
        "volatility": [0.10, 0.10], "amount20": [3e7, 3e7],
        "momentum_20": [0.02, 0.02], "momentum_60": [0.05, 0.05],
    })
    financials = pd.DataFrame({
        "symbol": ["600001.SH", "600002.SH"], "announcement_date": ["2024-04-01"] * 2,
        "bvps": [5.0, 5.0], "revenue": [2e9, 2e9], "net_profit": [2e8, 2e8],
        "roe": [10.0, 10.0], "total_shares": [2e9, 0.5e9], "total_assets": [4e9, 2e10],
    })
    cfg = {"price_max": 50, "pb_min": 0, "pb_max": 3, "revenue_min": 1e9,
           "low_vol_quantile": 1.0, "volatility_window": 60,
           "market_cap_min": 1e10, "roa_min": 0.03, "amount20_min": 2e7,
           "holdings": {"cold": 1, "warm": 1, "overheated": 1}}
    selection = select_blue_chips(date, snapshot, financials, "warm", cfg)
    assert selection.symbols == ["600001.SH"]
    evidence = selection.evidence.set_index("symbol")
    assert bool(evidence.loc["600001.SH", "eligible"])
    assert not bool(evidence.loc["600002.SH", "eligible"])
