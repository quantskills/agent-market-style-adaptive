from __future__ import annotations

import numpy as np
import pandas as pd

from agent_market_style_adaptive.strategy_common import (
    Selection, _as_date, point_in_time_latest, _tradable_snapshot,
)

def select_etfs(as_of, fund_history: pd.DataFrame, cfg: dict) -> Selection:
    data = fund_history.copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data[(data["date"] <= _as_date(as_of)) & data["symbol"].isin(cfg["symbols"])]
    rows: list[dict] = []
    windows = [int(value) for value in cfg["momentum_windows"]]
    weights = [float(value) for value in cfg["momentum_weights"]]
    minimum = max(windows) + 1
    for symbol in cfg["symbols"]:
        item = data[data["symbol"] == symbol].sort_values("date").drop_duplicates("date", keep="last")
        row = {"symbol": symbol, "history_days": len(item), "eligible": False, "score": np.nan}
        if len(item) >= minimum:
            price_column = "adj_close" if "adj_close" in item.columns else "close"
            close = item[price_column].astype(float)
            amount = item["amount"].astype(float)
            returns = [float(close.iloc[-1] / close.iloc[-window - 1] - 1) for window in windows]
            score = float(sum(weight * value for weight, value in zip(weights, returns)))
            ma_short = float(close.iloc[-int(cfg["short_ma"]):].mean())
            ma_long = float(close.iloc[-int(cfg["long_ma"]):].mean())
            amount_short = float(amount.iloc[-int(cfg["amount_short"]):].mean())
            amount_long = float(amount.iloc[-int(cfg["amount_long"]):].mean())
            ret3 = float(close.iloc[-1] / close.iloc[-4] - 1)
            checks = {
                "three_day_ok": ret3 >= float(cfg["three_day_floor"]),
                "momentum_ok": score > float(cfg.get("minimum_score", 0.0)),
                "all_windows_ok": (all(value > 0 for value in returns) if cfg.get("require_all_momentum_positive", False) else True),
                "above_ma_ok": (float(close.iloc[-1]) > ma_short if cfg.get("require_above_short_ma", True) else True),
                "ma_order_ok": (ma_short > ma_long if cfg.get("require_ma_order", True) else True),
                "amount_ok": (amount_short > amount_long if cfg.get("require_amount_confirmation", True) else True),
            }
            row.update(checks | {"ret3": ret3, "score": score, "ma20": ma_short, "ma60": ma_long,
                                  "amount5": amount_short, "amount20": amount_long,
                                  "eligible": all(checks.values())})
        rows.append(row)
    evidence = pd.DataFrame(rows)
    # Cross-sectional relative strength is computed only from information
    # available on the signal date.  Ranking all funds with sufficient history
    # avoids turning the current winners into a present-day survivorship list.
    comparable = evidence["score"].notna()
    evidence["relative_strength_pct"] = np.nan
    evidence.loc[comparable, "relative_strength_pct"] = evidence.loc[comparable, "score"].rank(
        pct=True, method="average"
    )
    if "relative_strength_min_percentile" in cfg:
        evidence["relative_strength_ok"] = (
            evidence["relative_strength_pct"] >= float(cfg["relative_strength_min_percentile"])
        )
        evidence["eligible"] &= evidence["relative_strength_ok"].fillna(False)
    selected = evidence[evidence["eligible"]].sort_values(["score", "symbol"], ascending=[False, True])
    return Selection(selected.head(int(cfg["max_positions"]))["symbol"].tolist(), evidence)


def etf_stop(entry_price: float, peak_close: float, close: float, cfg: dict, ret3: float | None = None) -> tuple[bool, float, str]:
    """Optional fixed and trailing ETF exits; disabled when no stop is configured."""
    stops: list[tuple[float, str]] = []
    if ret3 is not None and np.isfinite(ret3) and ret3 < float(cfg.get("three_day_floor", -np.inf)):
        return True, float(close), "three_day_floor"
    if "fixed_stop" in cfg:
        stops.append((entry_price * (1 - float(cfg["fixed_stop"])), "fixed"))
    if "trailing_stop" in cfg:
        trigger = float(cfg.get("trailing_trigger", 0.0))
        if peak_close >= entry_price * (1 + trigger):
            stops.append((peak_close * (1 - float(cfg["trailing_stop"])), "trailing"))
    if not stops:
        return False, float("nan"), "disabled"
    stop_price, reason = max(stops, key=lambda item: item[0])
    return bool(close <= stop_price), float(stop_price), reason
