from __future__ import annotations

import numpy as np
import pandas as pd

from agent_market_style_adaptive.strategy_common import (
    Selection, _as_date, point_in_time_latest, _tradable_snapshot,
)

UNQUALIFIED_OPINIONS = {
    "unqualified_opinion", "standard_unqualified_opinion", "标准无保留意见", "无保留意见",
}

def holding_count_from_deviation(index_history: pd.DataFrame, cfg: dict) -> tuple[int, float]:
    history = index_history.sort_values("date")
    close = history["close"].astype(float)
    window = int(cfg["ma_window"])
    if len(close) < window:
        return int(cfg["holdings_normal"]), np.nan
    deviation = float(close.iloc[-1] / close.iloc[-window:].mean() - 1.0)
    if deviation > float(cfg["high_deviation"]):
        return int(cfg["holdings_high"]), deviation
    if deviation < float(cfg["low_deviation"]):
        return int(cfg["holdings_low"]), deviation
    return int(cfg["holdings_normal"]), deviation

def select_small_cap(
    as_of,
    stock_snapshot: pd.DataFrame,
    fundamentals: pd.DataFrame,
    audit: pd.DataFrame,
    dividends: pd.DataFrame,
    index_history: pd.DataFrame,
    cfg: dict,
) -> Selection:
    # ``joinquant_replica`` intentionally keeps the selector faithful to the
    # supplied JoinQuant code.  The production/default mode below remains the
    # stricter research selector (financial, audit and dividend gates).  This
    # switch is explicit so the report can distinguish a code replica from the
    # enhanced point-in-time sleeve.
    replica = str(cfg.get("replica_mode", "")).lower() == "joinquant_replica"
    pit = point_in_time_latest(fundamentals, as_of)
    snapshot = stock_snapshot.copy()
    merged = snapshot.merge(pit, on="symbol", how="left", suffixes=("", "_fund"))
    universe_symbols = cfg.get("universe_symbols")
    merged["index_universe_ok"] = True if universe_symbols is None else merged["symbol"].isin(set(universe_symbols))

    # The reference strategy first narrows the CSI1000 universe by circulating
    # market cap, then applies the yesterday-limit and total-market-cap gates.
    # ``float_shares_asof`` is a point-in-time PandaData snapshot supplied by
    # the orchestrator.  When it is unavailable we retain an explicit total
    # share proxy in the evidence instead of silently inventing float shares.
    float_map = cfg.get("float_shares_asof", {}) or {}
    merged["float_shares"] = merged["symbol"].map(float_map)
    merged["float_shares_source"] = np.where(merged["float_shares"].notna(), "pandadata_free_circulation", "total_shares_proxy")
    merged["float_shares"] = merged["float_shares"].fillna(merged["total_shares"])
    merged["circulating_market_cap"] = merged["close"] * merged["float_shares"]
    merged["market_cap"] = merged["close"] * merged["total_shares"]
    merged["pb"] = merged["close"] / merged["bvps"].replace(0, np.nan)
    merged["roa"] = merged["net_profit"] / merged["total_assets"].replace(0, np.nan)

    symbol = merged["symbol"].astype(str)
    name = merged.get("name", pd.Series("", index=merged.index)).fillna("").astype(str)
    has_listing_field = "list_date" in merged.columns
    list_date = pd.to_datetime(merged.get("list_date", pd.Series(pd.NaT, index=merged.index)), errors="coerce")
    # The production cache always contains security_master.list_date.  Small
    # synthetic/legacy fixtures may not; in that case leave this optional gate
    # neutral instead of treating missing metadata as a newly listed stock.
    merged["listing_ok"] = (
        list_date.notna() & ((_as_date(as_of) - list_date).dt.days >= int(cfg.get("min_listing_days", 375)))
        if has_listing_field else True
    )
    merged["board_ok"] = ~symbol.str.startswith(("300", "301", "688", "689", "4", "8"))
    merged["name_ok"] = ~name.str.upper().str.contains("ST|退", regex=True)
    merged["tradable_ok"] = _tradable_snapshot(merged, as_of)
    merged["price_ok"] = merged["close"] <= float(cfg.get("max_price", 20.0))
    merged["universe_ok"] = merged[["index_universe_ok", "listing_ok", "board_ok", "name_ok", "tradable_ok", "price_ok"]].all(axis=1)

    pre = merged[merged["universe_ok"]].copy()
    float_limit = int(cfg.get("preselect_float_cap_count", 200))
    if float_limit and len(pre) > float_limit:
        pre = pre.nsmallest(float_limit, "circulating_market_cap")
    prev_close = pd.to_numeric(pre["prev_close"] if "prev_close" in pre else pd.Series(np.nan, index=pre.index), errors="coerce")
    prev_up = pd.to_numeric(pre["prev_limit_up"] if "prev_limit_up" in pre else pd.Series(np.nan, index=pre.index), errors="coerce")
    prev_down = pd.to_numeric(pre["prev_limit_down"] if "prev_limit_down" in pre else pd.Series(np.nan, index=pre.index), errors="coerce")
    pre["yesterday_limit_up"] = prev_close.notna() & prev_up.notna() & (prev_close >= prev_up * float(cfg.get("limit_buffer", 0.995)))
    pre["yesterday_limit_down"] = prev_close.notna() & prev_down.notna() & (prev_close <= prev_down * float(cfg.get("limit_buffer", 1.005)))
    pre = pre[~pre["yesterday_limit_up"] & ~pre["yesterday_limit_down"]]
    total_limit = int(cfg.get("preselect_total_cap_count", 100))
    if total_limit and len(pre) > total_limit:
        pre = pre.nsmallest(total_limit, "market_cap")

    merged["preselected"] = merged["symbol"].isin(pre["symbol"])
    if replica:
        # The reference small-cap routine has no fundamental, audit or
        # dividend clause.  Fundamentals are still merged into the evidence
        # ledger when present, but they must not silently change eligibility.
        fundamental_ok = pd.Series(True, index=pre.index)
    else:
        fundamental_ok = (
            pre["market_cap"].between(float(cfg["market_cap_min"]), float(cfg["market_cap_max"]), inclusive="both")
            & (pre["revenue"] > float(cfg["revenue_min"]))
            & (pre["net_profit"] > float(cfg.get("net_profit_min", 2_000_000.0)))
            & (pre[["roe", "roa", "net_profit"]] > 0).all(axis=1)
        )
    pre["fundamental_ok"] = fundamental_ok.fillna(False)
    # Keep every candidate's fundamental verdict in the evidence ledger. The
    # execution universe is narrowed further by audit/dividend gates below,
    # but those gates must not erase the diagnostic reason for rejection.
    fundamental_map = pre.set_index("symbol")["fundamental_ok"].to_dict()
    eligible = pre[pre["fundamental_ok"]].copy()

    audits = audit.copy()
    if replica or audits.empty:
        good_audit: set[str] = set()
    else:
        audits["date"] = pd.to_datetime(audits["date"])
        audits = audits[(audits["date"] < _as_date(as_of)) & (audits["audit_type"] == "financial_statements")]
        if "quarter" in audits:
            audits = audits[audits["quarter"].astype(str).str.lower().str.endswith("q4")]
        audits = audits.sort_values(["symbol", "date"]).groupby("symbol", as_index=False).tail(1)
        opinion = audits["opinion"].astype(str).str.lower()
        strict = opinion.isin(UNQUALIFIED_OPINIONS)
        # The reference JoinQuant code does not use an audit-opinion field.
        # ``proxy_or_strict`` is therefore an explicit reproducibility mode:
        # it permits the cache's annual-report-available proxy while retaining
        # the evidence in the selection ledger.  It must never be described as
        # a real audit opinion (the research boundary documents this gap).
        if cfg.get("audit_mode", "strict") == "proxy_or_strict":
            strict |= audits.get("audit_proxy", pd.Series(False, index=audits.index)).fillna(False).astype(bool)
            strict |= opinion.eq("annual_report_available_proxy")
        good_audit = set(audits.loc[strict, "symbol"])

    divs = dividends.copy()
    if replica or divs.empty:
        recent_dividend: set[str] = set()
    else:
        divs["announcement_date"] = pd.to_datetime(divs["announcement_date"])
        cutoff = _as_date(as_of) - pd.DateOffset(years=int(cfg["dividend_lookback_years"]))
        mask = (divs["announcement_date"] < _as_date(as_of)) & (divs["announcement_date"] >= cutoff)
        if "div_type" in divs:
            mask &= divs["div_type"].astype(str).str.contains("cash|现金|派", case=False, regex=True)
        recent_dividend = set(divs.loc[mask, "symbol"])

    audit_mode = "none" if replica else str(cfg.get("audit_mode", "strict"))
    merged["audit_ok"] = True if audit_mode == "none" else merged["symbol"].isin(good_audit)
    merged["audit_mode"] = str(cfg.get("audit_mode", "strict"))
    merged["audit_proxy_used"] = bool(cfg.get("audit_mode", "strict") == "proxy_or_strict")
    merged["dividend_ok"] = (
        merged["symbol"].isin(recent_dividend)
        if (not replica and cfg.get("require_recent_dividend", True))
        else True
    )
    merged["fundamental_ok"] = merged["symbol"].map(fundamental_map).fillna(False).astype(bool)
    merged["eligible"] = merged["symbol"].isin(eligible["symbol"]) & merged["audit_ok"] & merged["dividend_ok"]
    dynamic_count, deviation = holding_count_from_deviation(index_history, cfg)
    # ``stock_num`` remains a backwards-compatible fixed-count override.  The
    # structural replica enables dynamic holdings explicitly so strong markets
    # concentrate and weak/uncertain markets diversify.
    count = dynamic_count if (not replica and cfg.get("dynamic_holdings", False)) else int(cfg.get("stock_num", dynamic_count))
    merged["ma10_deviation"] = deviation
    merged["target_count"] = count
    eligible = merged[merged["eligible"]].copy()
    industry_map = cfg.get("industry_by_symbol", {}) or {}
    eligible["industry"] = eligible["symbol"].map(industry_map).fillna(eligible.get("board", "MainBoard"))
    # The JoinQuant implementation keeps the smallest name in each SW L2
    # industry and returns a 2x buffer for the execution layer.
    eligible = eligible.sort_values(["market_cap", "symbol"], ascending=[True, True])
    eligible = eligible.loc[~eligible["industry"].duplicated(keep="first")]
    eligible["score"] = -eligible["market_cap"].rank(method="first", ascending=True)
    pool_count = max(count, count * int(cfg.get("candidate_buffer", 2)))
    selected = eligible.head(pool_count)
    skip_months = {int(month) for month in cfg.get("skip_months", [])}
    if pd.Timestamp(as_of).month in skip_months:
        merged["calendar_skip"] = True
        selected = eligible.iloc[0:0]
    else:
        merged["calendar_skip"] = False
    merged = merged.merge(eligible[["symbol", "industry", "score"]], on="symbol", how="left")
    return Selection(selected["symbol"].tolist(), merged.sort_values("symbol"))

def average_true_range(history: pd.DataFrame, window: int = 14) -> float:
    data = history.sort_values("date").tail(window + 1)
    if len(data) < window + 1:
        return np.nan
    previous = data["close"].shift(1)
    true_range = pd.concat(
        [(data["high"] - data["low"]).abs(), (data["high"] - previous).abs(), (data["low"] - previous).abs()], axis=1
    ).max(axis=1)
    return float(true_range.tail(window).mean())

def small_cap_stop(entry_price: float, peak_close: float, close: float, atr: float, cfg: dict) -> tuple[bool, float, str]:
    stops = [(entry_price * (1 - float(cfg["fixed_stop"])), "fixed")]
    if peak_close >= entry_price * (1 + float(cfg["protect_trigger"])):
        stops.append((entry_price * (1 + float(cfg["protect_floor"])), "profit_protection"))
    if np.isfinite(atr):
        stops.append((peak_close - float(cfg["atr_multiple"]) * atr, "atr"))
    stop_price, reason = max(stops, key=lambda item: item[0])
    return bool(close <= stop_price), float(stop_price), reason
