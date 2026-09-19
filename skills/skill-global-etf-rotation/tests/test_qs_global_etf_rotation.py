import numpy as np
import pandas as pd
from qs_global_etf_rotation import select_etfs

def test_future_prices_cannot_change_current_selection():
    dates = pd.bdate_range("2024-01-01", periods=131)
    frame = pd.DataFrame({"date": dates, "symbol": "518880.SH", "close": np.linspace(1, 2, 131), "amount": np.linspace(100, 500, 131)})
    cfg = {"symbols": ["518880.SH", "513100.SH"], "momentum_windows": [20, 60, 120], "momentum_weights": [.5,.3,.2], "short_ma": 20, "long_ma": 60, "amount_short": 5, "amount_long": 20, "three_day_floor": -.05, "max_positions": 2}
    before = select_etfs(dates[-2], frame, cfg)
    frame.loc[130, ["close", "amount"]] = [0.001, 1e10]
    after = select_etfs(dates[-2], frame, cfg)
    assert before.symbols == after.symbols == ["518880.SH"]
    pd.testing.assert_frame_equal(before.evidence, after.evidence)
    assert not after.evidence.set_index("symbol").loc["513100.SH", "eligible"]


def test_trend_confirmation_can_be_relaxed_without_changing_defaults():
    dates = pd.bdate_range("2024-01-01", periods=131)
    frame = pd.DataFrame({
        "date": dates,
        "symbol": "518880.SH",
        "close": np.linspace(2.0, 1.0, 131),
        "amount": np.linspace(100.0, 200.0, 131),
    })
    cfg = {
        "symbols": ["518880.SH"], "momentum_windows": [20, 60, 120],
        "momentum_weights": [.5, .3, .2], "short_ma": 20, "long_ma": 60,
        "amount_short": 5, "amount_long": 20, "three_day_floor": -1.0,
        "minimum_score": -1.0, "max_positions": 1,
        "require_amount_confirmation": False,
    }
    assert select_etfs(dates[-1], frame, cfg).symbols == []
    cfg.update({"require_above_short_ma": False, "require_ma_order": False})
    assert select_etfs(dates[-1], frame, cfg).symbols == ["518880.SH"]
