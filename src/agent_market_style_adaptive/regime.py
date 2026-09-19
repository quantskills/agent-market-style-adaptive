from __future__ import annotations

import numpy as np
import pandas as pd


REGIME_NAMES = ("overheated", "warm", "cold", "crisis")


def classify_regimes(index_daily: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Return look-ahead-free raw and confirmed regimes indexed by trading date."""
    frame = index_daily.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").set_index("date")
    close = frame["close"].astype(float)
    trend_window = int(config["trend_window"])
    long_ma_window = int(config["long_ma"])
    vol_window = int(config["vol_window"])

    ret = close.pct_change(trend_window, fill_method=None)
    long_ma = close.rolling(long_ma_window, min_periods=long_ma_window).mean()
    long_ma_rising = long_ma.diff() > 0
    trend = pd.Series("sideways", index=close.index, dtype="object")
    trend[(ret > float(config["bull_threshold"])) & long_ma_rising] = "bull"
    trend[(ret < float(config["bear_threshold"])) & ~long_ma_rising] = "bear"

    log_ret = np.log(close / close.shift(1))
    vol = log_ret.rolling(vol_window, min_periods=vol_window).std(ddof=1) * np.sqrt(252)
    low = vol.expanding(min_periods=vol_window).quantile(float(config["vol_low_quantile"]))
    high = vol.expanding(min_periods=vol_window).quantile(float(config["vol_high_quantile"]))
    vol_state = pd.Series("normal", index=close.index, dtype="object")
    vol_state[vol < low] = "low"
    vol_state[vol > high] = "high"

    raw = pd.Series("warm", index=close.index, dtype="object")
    raw[(trend == "bull") & (vol_state != "high")] = "overheated"
    raw[(trend == "bear") | (vol_state == "high")] = "cold"
    raw[(trend == "bear") & (vol_state == "high")] = "crisis"
    confirmed = confirm_regimes(raw, int(config["confirmation_days"]))
    return pd.DataFrame(
        {"close": close, "trend_return": ret, "long_ma": long_ma, "volatility": vol,
         "trend": trend, "vol_state": vol_state, "raw_regime": raw, "regime": confirmed}
    ).reset_index()


def confirm_regimes(raw: pd.Series, confirmation_days: int = 3) -> pd.Series:
    """Confirm ordinary changes after N consecutive observations; crisis is immediate."""
    if confirmation_days < 1:
        raise ValueError("confirmation_days must be positive")
    current = "warm"
    candidate = None
    count = 0
    output: list[str] = []
    for value in raw.fillna("warm"):
        if value == "crisis":
            current, candidate, count = "crisis", None, 0
        elif value == current:
            candidate, count = None, 0
        elif value == candidate:
            count += 1
            if count >= confirmation_days:
                current, candidate, count = value, None, 0
        else:
            candidate, count = value, 1
            if confirmation_days == 1:
                current, candidate, count = value, None, 0
        output.append(current)
    return pd.Series(output, index=raw.index, dtype="object")

