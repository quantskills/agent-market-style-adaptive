from __future__ import annotations

import numpy as np
import pandas as pd

from agent_market_style_adaptive.strategy_common import (
    Selection, _as_date, point_in_time_latest, _tradable_snapshot,
)


def blue_chip_stop(entry_price: float, close: float, cfg: dict) -> tuple[bool, float, str]:
    """White-horse fixed stop from the supplied JoinQuant strategy."""
    stop = float(entry_price) * (1.0 - float(cfg.get("fixed_stop", cfg.get("stop_loss", 0.06))))
    return bool(float(close) <= stop), stop, "fixed"


def _state_name(sentiment: str) -> str:
    # Keep the repository's three-state vocabulary.  ``hot`` is accepted as a
    # legacy alias for the supplied JoinQuant code's ``overheated`` state.
    return {"crisis": "cold", "hot": "overheated"}.get(str(sentiment), str(sentiment))


def select_blue_chips(
    as_of, stock_history: pd.DataFrame, fundamentals: pd.DataFrame, sentiment: str, cfg: dict
) -> Selection:
    """Replicate the supplied white-horse selector with point-in-time data."""
    replica = str(cfg.get("replica_mode", "")).lower() == "joinquant_replica"
    state = _state_name(sentiment)
    state_cfg = cfg.get("regime_thresholds", {}).get(state, {})
    data = stock_history.copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data[data["date"] <= _as_date(as_of)]
    latest = data.sort_values(["symbol", "date"]).groupby("symbol", as_index=False).tail(1)
    pit = point_in_time_latest(fundamentals, as_of)
    merged = latest.merge(pit, on="symbol", how="left", suffixes=("", "_fund"))
    merged["pb"] = merged["close"] / merged["bvps"].replace(0, np.nan)
    assets = pd.to_numeric(merged.get("total_assets", pd.Series(np.nan, index=merged.index)), errors="coerce")
    merged["roa"] = pd.to_numeric(merged["net_profit"], errors="coerce") / assets.replace(0, np.nan)
    # Older caches and compact fixtures may not carry total_assets. ROE is a
    # documented quality proxy in that case; it is never used when assets are
    # available in the production point-in-time cache.
    merged["roa"] = merged["roa"].where(assets.notna(), pd.to_numeric(merged.get("roe", np.nan), errors="coerce") / 100.0)
    has_revenue_yoy = "revenue_yoy" in merged.columns
    has_profit_yoy = "net_profit_yoy" in merged.columns
    revenue_yoy = pd.to_numeric(merged.get("revenue_yoy", pd.Series(np.nan, index=merged.index)), errors="coerce")
    net_profit_yoy = pd.to_numeric(merged.get("net_profit_yoy", pd.Series(np.nan, index=merged.index)), errors="coerce")
    merged["revenue_yoy"] = revenue_yoy
    merged["net_profit_yoy"] = net_profit_yoy

    symbols = merged["symbol"].astype(str)
    names = merged.get("name", pd.Series("", index=merged.index)).fillna("").astype(str)
    universe_symbols = cfg.get("universe_symbols")
    index_ok = True if universe_symbols is None else merged["symbol"].isin(set(universe_symbols))
    excluded = symbols.str.startswith(("300", "301", "688", "689", "4", "8")) if cfg.get("exclude_growth_boards", True) else False
    merged["index_universe_ok"] = index_ok
    merged["board_ok"] = ~excluded
    merged["name_ok"] = ~names.str.upper().str.contains("ST|退", regex=True)
    # The pasted JoinQuant implementation applies the 50-yuan ceiling to its
    # small-index branch; the CSI300 branch has no price predicate.  Keep the
    # historical default (price-filtered) for standalone callers, while the
    # exact replica config opts out for ``big`` explicitly.
    # The supplied JoinQuant code applies the 50-yuan ceiling to the small
    # branch, but does not apply it to the large-index branch.  Standalone
    # callers retain the historical filtered default; replica configs opt out
    # explicitly via ``price_filter_big: false``.
    price_filter = cfg.get("white_horse_signal") != "big" or bool(cfg.get("price_filter_big", True))
    merged["price_ok"] = (
        merged["close"] <= float(state_cfg.get("price_max", cfg.get("price_max", 50.0)))
        if price_filter else True
    )
    merged["tradable_ok"] = _tradable_snapshot(merged, as_of)
    shares = pd.to_numeric(merged.get("total_shares", pd.Series(np.nan, index=merged.index)), errors="coerce")
    merged["market_cap"] = merged["close"] * shares
    # The source code uses a different universe for the ``small`` signal and
    # only applies its 5--60 (hundred-million yuan) market-cap range there.
    # The ``big`` branch is an index universe with no market-cap floor/ceiling.
    market_cap_min = None if replica and cfg.get("white_horse_signal") == "big" else cfg.get("market_cap_min")
    market_cap_max = None if replica and cfg.get("white_horse_signal") == "big" else cfg.get("market_cap_max")
    market_cap_ok = pd.Series(True, index=merged.index)
    if market_cap_min is not None:
        market_cap_ok &= merged["market_cap"] >= float(market_cap_min)
    if market_cap_max is not None:
        market_cap_ok &= merged["market_cap"] <= float(market_cap_max)
    roa_min = cfg.get("roa_min")
    roa_ok = pd.Series(True, index=merged.index) if roa_min is None else merged["roa"] >= float(roa_min)
    amount_min = cfg.get("amount20_min")
    amount_ok = pd.Series(True, index=merged.index) if amount_min is None else pd.to_numeric(merged.get("amount20", np.nan), errors="coerce") >= float(amount_min)
    merged["base_ok"] = merged[["index_universe_ok", "board_ok", "name_ok", "price_ok", "tradable_ok"]].all(axis=1)
    merged["market_cap_ok"] = market_cap_ok
    merged["roa_ok"] = roa_ok
    merged["amount20_ok"] = amount_ok

    allowed_regimes = cfg.get("allowed_regimes")
    if allowed_regimes and state not in set(map(str, allowed_regimes)):
        merged["eligible"] = False
        merged["target_count"] = int(cfg.get("stock_num", 5))
        merged["market_state"] = state
        return Selection([], merged.sort_values("symbol"))

    pb_min = float(state_cfg.get("pb_min", cfg.get("pb_min", 0.0)))
    pb_max = state_cfg.get("pb_max", cfg.get("pb_max"))
    revenue_min = float(state_cfg.get("revenue_min", cfg.get("revenue_min", 0.0)))
    profit_min = float(state_cfg.get("net_profit_min", cfg.get("net_profit_min", 0.0)))
    revenue_yoy_key = "revenue_yoy_min" in state_cfg or "revenue_yoy_min" in cfg
    profit_yoy_key = "net_profit_yoy_min" in state_cfg or "net_profit_yoy_min" in cfg
    revenue_yoy_min = float(state_cfg.get("revenue_yoy_min", cfg.get("revenue_yoy_min", -np.inf)))
    profit_yoy_min = float(state_cfg.get("net_profit_yoy_min", cfg.get("net_profit_yoy_min", -np.inf)))
    valuation_ok = merged["pb"] > pb_min
    if pb_max is not None:
        valuation_ok &= merged["pb"] <= float(pb_max)
    if replica and str(cfg.get("white_horse_signal", "")) == "small":
        # JoinQuant's small branch uses only the small-index universe, a
        # 5--60 hundred-million-yuan market-cap range and a price cap.  It
        # does not add the big-branch ROA/revenue/profit quality gates.
        merged["fundamental_ok"] = merged["base_ok"] & market_cap_ok
    elif replica:
        # JoinQuant's big branch filters on PB and year-over-year revenue and
        # profit growth.  Absolute revenue/profit/ROE/ROA gates belong to the
        # enhanced sleeve, not to the reference code.
        merged["fundamental_ok"] = (
            merged["base_ok"] & valuation_ok
            & ((merged["revenue_yoy"] > revenue_yoy_min) if has_revenue_yoy and revenue_yoy_key else True)
            & ((merged["net_profit_yoy"] > profit_yoy_min) if has_profit_yoy and profit_yoy_key else True)
        )
    else:
        merged["fundamental_ok"] = (
            merged["base_ok"] & market_cap_ok & roa_ok & amount_ok & valuation_ok
            & (merged["revenue"] > revenue_min)
            & (merged["net_profit"] > profit_min)
            & (merged["roe"] > 0) & (merged["roa"] > 0)
            & ((merged["revenue_yoy"] > revenue_yoy_min) if has_revenue_yoy and revenue_yoy_key else True)
            & ((merged["net_profit_yoy"] > profit_yoy_min) if has_profit_yoy and profit_yoy_key else True)
        )
    base = merged[merged["fundamental_ok"]].copy()
    if base.empty:
        merged["eligible"] = False
        merged["target_count"] = int(cfg.get("stock_num", 5))
        return Selection([], merged.sort_values("symbol"))

    if replica and str(cfg.get("white_horse_signal", "")) == "small":
        base = base.sort_values(["market_cap", "symbol"], ascending=[True, True])
        base["score"] = -base["market_cap"].rank(method="first", ascending=True)
    else:
        base["score"] = base["roa"] if state == "overheated" else base["roa"] / base["pb"].replace(0, np.nan)
    base["score"] = base["score"].replace([np.inf, -np.inf], np.nan)
    base = base.dropna(subset=["score"]).sort_values(["score", "symbol"], ascending=[False, True])
    holdings_cfg = state_cfg.get("holdings", cfg.get("holdings", {}))
    count = int(holdings_cfg.get(state, cfg.get("stock_num", 5))) if isinstance(holdings_cfg, dict) else int(cfg.get("stock_num", 5))
    pool_count = max(count, count * int(cfg.get("candidate_buffer", 1)))
    selected = base.head(pool_count)
    merged["eligible"] = merged["symbol"].isin(base["symbol"])
    merged["score"] = merged["symbol"].map(base.set_index("symbol")["score"])
    merged["target_count"] = count
    merged["market_state"] = state
    return Selection(selected["symbol"].tolist(), merged.sort_values("symbol"))
