from __future__ import annotations

from html import escape
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .metrics import performance_metrics, period_metrics


SLEEVES = ("small_cap", "etf", "blue_chip")
SLEEVE_LABELS = {"small_cap": "小市值", "etf": "全球 ETF", "blue_chip": "低波白马"}
TARGET_ANNUAL_RETURN = 0.20


def _read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.is_file() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kwargs)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def compare_runs(output_dir: str | Path, annualization: int = 252) -> pd.DataFrame:
    root = Path(output_dir)
    rows = []
    for mode in ("adaptive",):
        frame = pd.read_csv(root / mode / "equity.csv", parse_dates=["date"]).set_index("date")
        metrics = performance_metrics(frame["nav"], frame["benchmark"], annualization)
        trades = _read_csv(root / mode / "trades.csv")
        metrics["transaction_cost"] = float(trades["cost"].sum()) if not trades.empty else 0.0
        metrics["turnover"] = float(trades["gross"].sum() / frame["nav"].mean()) if not trades.empty else 0.0
        metrics["target_20pct_met"] = bool(metrics["annual_return"] >= TARGET_ANNUAL_RETURN)
        rows.append({"mode": mode} | metrics)
    comparison = pd.DataFrame(rows)
    comparison.to_csv(root / "comparison.csv", index=False)
    return comparison


def _independent_metrics(root: Path, annualization: int) -> pd.DataFrame:
    rows = []
    for sleeve in SLEEVES:
        path = root / "independent" / sleeve / "equity.csv"
        if not path.is_file():
            continue
        frame = pd.read_csv(path, parse_dates=["date"]).set_index("date")
        metrics = performance_metrics(frame["nav"], frame["benchmark"], annualization)
        trades = _read_csv(path.parent / "trades.csv")
        metrics["turnover"] = float(trades["gross"].sum() / frame["nav"].mean()) if not trades.empty else 0.0
        rows.append({"strategy": sleeve, "strategy_zh": SLEEVE_LABELS[sleeve], **metrics,
                     "target_20pct_met": bool(metrics["annual_return"] >= TARGET_ANNUAL_RETURN),
                     "trades": len(trades),
                     "transaction_cost": float(trades["cost"].sum()) if not trades.empty else 0.0})
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame.to_csv(root / "independent_strategies.csv", index=False)
    return frame


def _latest_holdings(root: Path) -> pd.DataFrame:
    holdings = _read_csv(root / "adaptive" / "holdings.csv", parse_dates=["date"])
    equity = _read_csv(root / "adaptive" / "equity.csv", parse_dates=["date"])
    columns = ["date", "symbol", "name", "name_asof_signal", "name_current", "name_source", "sleeve", "shares", "close", "market_value", "weight", "sleeve_zh"]
    if equity.empty:
        pd.DataFrame(columns=columns).to_csv(root / "latest_holdings.csv", index=False)
        return pd.DataFrame()

    # Use the last backtest date, including a fully-cash day after a stop exit.
    # Otherwise a prior position can be incorrectly presented as current.
    date = equity["date"].max()
    latest = holdings[holdings["date"].eq(date)].copy()
    nav = float(equity.loc[equity["date"].eq(date), "nav"].iloc[-1])
    if not latest.empty:
        latest["weight"] = latest["market_value"] / nav
        latest["sleeve_zh"] = latest["sleeve"].map(SLEEVE_LABELS)
        latest = latest.sort_values(["sleeve", "weight"], ascending=[True, False])
    else:
        latest = pd.DataFrame(columns=columns)
    latest.to_csv(root / "latest_holdings.csv", index=False)
    return latest


def _frequency_comparison(root: Path) -> pd.DataFrame:
    """Load the weekly versus semi-monthly adaptive experiment."""
    path = root.parents[1] / "rebalance_frequency_comparison_pandadata_exact_20260918" / "frequency_leaderboard.csv"
    frame = _read_csv(path) if path.is_file() else pd.DataFrame()
    if not frame.empty and "mode" in frame.columns:
        frame = frame[frame["mode"].astype(str).eq("adaptive")].copy()
    return frame

def _data_quality(root: Path) -> tuple[dict, str]:
    """Read the validation artifact without making reporting depend on it."""
    path = None
    snapshot = root / "adaptive" / "config.snapshot.yaml"
    try:
        cfg = yaml.safe_load(snapshot.read_text(encoding="utf-8")) or {}
        cache_dir = Path(cfg.get("data", {}).get("cache_dir", ""))
        if not cache_dir.is_absolute():
            cache_dir = root.parents[2] / cache_dir
        candidate = cache_dir / "data_dictionary.json"
        if candidate.is_file():
            path = candidate
    except (OSError, yaml.YAMLError):
        pass
    if path is None:
        candidate = root / "data_sync" / "data_dictionary.json"
        path = candidate if candidate.is_file() else None
    if path is None or not path.is_file():
        return {}, "未生成 data_dictionary.json"
    try:
        quality = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, "data_dictionary.json 无法读取"
    stock = quality.get("tables", {}).get("stock_daily", {})
    jumps = stock.get("adjusted_jumps_over_50pct")
    warning = f"股票复权价格有 {int(jumps)} 处单日跳变超过 50%" if jumps is not None else "股票复权价格跳变统计未生成"
    return quality, warning


def _latest_allocation_summary(root: Path, latest: pd.DataFrame, latest_date: pd.Timestamp | None = None) -> pd.DataFrame:
    """Reconcile target sleeve budgets with the actually invested NAV.

    ``allocation_history.csv`` records the structural target (40/40/20), while
    risk overlays, stop exits, unavailable candidates and execution blocks can
    leave part of that budget in cash.  The report must show both quantities so
    that a fixed budget is not mistaken for a fully invested position.
    """
    equity = _read_csv(root / "adaptive" / "equity.csv", parse_dates=["date"])
    if equity.empty:
        return pd.DataFrame()
    date = latest_date or equity["date"].max()
    nav_rows = equity[equity["date"].eq(date)]
    if nav_rows.empty:
        nav_rows = equity.tail(1)
        date = nav_rows["date"].iloc[0]
    nav = float(nav_rows["nav"].iloc[-1])
    invested = latest.groupby("sleeve", dropna=False)["market_value"].sum() if not latest.empty else pd.Series(dtype=float)

    allocations = _read_csv(root / "adaptive" / "allocation_history.csv", parse_dates=["date"])
    budget = {}
    if not allocations.empty:
        row = allocations[allocations["date"].le(date)].tail(1)
        if row.empty:
            row = allocations.tail(1)
        budget = row.iloc[0].to_dict()
    rows = []
    for sleeve in SLEEVES:
        actual_value = float(invested.get(sleeve, 0.0))
        target_weight = float(budget.get(sleeve, np.nan))
        actual_weight = actual_value / nav if nav else np.nan
        rows.append({"date": date, "asset": SLEEVE_LABELS[sleeve], "target_budget": target_weight,
                     "actual_weight": actual_weight, "weight_gap": actual_weight - target_weight,
                     "market_value": actual_value})
    cash_value = float(nav - sum(row["market_value"] for row in rows))
    rows.append({"date": date, "asset": "现金", "target_budget": float(budget.get("cash", 0.0)),
                 "actual_weight": cash_value / nav if nav else np.nan,
                 "weight_gap": cash_value / nav - float(budget.get("cash", 0.0)) if nav else np.nan,
                 "market_value": cash_value})
    return pd.DataFrame(rows)


def _holding_changes(root: Path) -> pd.DataFrame:
    trades = _read_csv(root / "adaptive" / "trades.csv", parse_dates=["date", "signal_date"])
    holdings = _read_csv(root / "adaptive" / "holdings.csv", parse_dates=["date"])
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for date, group in trades.groupby("date", sort=True):
        # Resolve labels from the latest holdings snapshot at or before the
        # execution date. This prevents a later rename/ST label from being
        # shown for an earlier buy or sell.
        names = {}
        if not holdings.empty and "name" in holdings:
            prior = holdings[holdings["date"].le(date)].sort_values("date")
            if not prior.empty:
                prior = prior.drop_duplicates("symbol", keep="last")
                names = prior.set_index("symbol")["name"].fillna("").to_dict()
        def describe(side: str) -> str:
            symbols = group.loc[group["side"].eq(side), "symbol"].drop_duplicates().tolist()
            return "；".join(f"{symbol} {names.get(symbol, '')}".strip() for symbol in symbols) or "—"
        after = holdings[holdings["date"].eq(date)] if not holdings.empty else pd.DataFrame()
        after_items = []
        for row in after.itertuples():
            values = row._asdict()
            name = values.get("name_asof_signal", values.get("name", ""))
            name = "" if pd.isna(name) else str(name)
            after_items.append(f"{values.get('symbol', '')} {name}".strip())
        after_text = "；".join(after_items) or "现金/无持仓"
        signal_dates = group["signal_date"].dropna()
        rows.append({"execution_date": date, "signal_date": signal_dates.min() if not signal_dates.empty else pd.NaT,
                     "regime": "", "buys": describe("buy"), "sells": describe("sell"),
                     "reasons": "；".join(sorted(group["reason"].dropna().astype(str).unique())),
                     "holdings_after": after_text})
    result = pd.DataFrame(rows)
    allocations = _read_csv(root / "adaptive" / "allocation_history.csv", parse_dates=["date"])
    if not allocations.empty:
        result = pd.merge_asof(result.sort_values("execution_date"), allocations[["date", "regime"]].sort_values("date"),
                               left_on="execution_date", right_on="date", direction="backward", suffixes=("", "_allocation"))
        result["regime"] = result["regime_allocation"].fillna(result["regime"])
        result = result.drop(columns=[column for column in ("date", "regime_allocation") if column in result])
    result.to_csv(root / "holding_changes.csv", index=False)
    return result


def _blocked_orders(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load execution-blocked orders and aggregate them for the report.

    The backtest keeps blocked orders separate from ``trades.csv`` because they
    did not execute and therefore must not enter turnover, costs, or holdings.
    Showing them explicitly in the HTML is important: a limit-up/limit-down or
    suspension can explain why the realized sleeve weight differs from its
    nominal target.
    """
    blocked = _read_csv(root / "adaptive" / "blocked_orders.csv", parse_dates=["date"])
    if blocked.empty:
        return blocked, pd.DataFrame()
    reason_labels = {
        "limit_up": "涨停无法买入",
        "limit_down": "跌停无法卖出",
        "suspended": "停牌",
        "missing_open": "缺少开盘价",
    }
    blocked["reason_zh"] = blocked["reason"].map(reason_labels).fillna(blocked["reason"])
    blocked["side_zh"] = blocked["side"].map({"buy": "买入", "sell": "卖出"}).fillna(blocked["side"])
    blocked.to_csv(root / "blocked_orders_report.csv", index=False)
    summary = (
        blocked.groupby(["reason", "reason_zh"], dropna=False)
        .agg(blocked_orders=("symbol", "size"), blocked_shares=("shares", "sum"))
        .reset_index()
        .sort_values("blocked_orders", ascending=False)
    )
    return blocked, summary


def _stop_summary(root: Path) -> pd.DataFrame:
    """Count executed and signalled stop exits by explicit reason."""
    trades = _read_csv(root / "adaptive" / "trades.csv")
    evidence = _read_csv(root / "adaptive" / "selection_evidence.csv")
    labels = {
        "stop_fixed": "固定止损",
        "stop_profit_protection": "盈利保护止损",
        "stop_atr": "ATR动态止损",
        "stop_three_day_floor": "ETF近3日跌幅止损",
        "stop_trailing": "ETF移动止损",
    }
    rows = []
    if not trades.empty and "reason" in trades:
        exits = trades[trades["reason"].astype(str).str.startswith("stop_")].copy()
        if not exits.empty:
            rows.append(exits["reason"].value_counts().rename_axis("reason").reset_index(name="executed_exits"))
    if rows:
        result = rows[0]
    else:
        result = pd.DataFrame(columns=["reason", "executed_exits"])
    if not evidence.empty and "event" in evidence and "reason" in evidence:
        signals = evidence[evidence["event"].eq("stop_signal")]["reason"].astype(str).value_counts()
        signal_frame = signals.rename_axis("reason").reset_index(name="stop_signals")
        result = result.merge(signal_frame, on="reason", how="outer")
    if result.empty:
        return result
    result["reason_zh"] = result["reason"].map(labels).fillna(result["reason"])
    return result[["reason", "reason_zh"] + [c for c in ("stop_signals", "executed_exits") if c in result]] \
        .fillna(0).sort_values("reason")


def _regime_summary(root: Path) -> pd.DataFrame:
    equity = pd.read_csv(root / "adaptive" / "equity.csv", parse_dates=["date"]).sort_values("date")
    equity["daily_return"] = equity["nav"].pct_change(fill_method=None)
    rows = []
    for regime, part in equity.groupby("regime", sort=False):
        returns = part["daily_return"].dropna()
        rows.append({"regime": regime, "days": len(part), "share": len(part) / len(equity),
                     "avg_daily_return": returns.mean(), "annualized_volatility": returns.std(ddof=1) * np.sqrt(252),
                     "average_cash": part["cash"].div(part["nav"]).replace([np.inf, -np.inf], np.nan).mean()})
    return pd.DataFrame(rows)


def _regime_contributions(root: Path) -> pd.DataFrame:
    """Attribute daily sleeve PnL to the signal-date market regime."""
    contribution = _read_csv(root / "adaptive" / "strategy_contributions.csv", parse_dates=["date"])
    equity = _read_csv(root / "adaptive" / "equity.csv", parse_dates=["date"])
    if contribution.empty or equity.empty:
        return pd.DataFrame()
    merged = contribution.merge(equity[["date", "regime"]], on="date", how="left")
    columns = [sleeve for sleeve in SLEEVES if sleeve in merged]
    if not columns:
        return pd.DataFrame()
    result = merged.groupby("regime", dropna=False)[columns].sum().reset_index()
    return result.rename(columns={sleeve: SLEEVE_LABELS[sleeve] for sleeve in columns})


def _fmt_table(frame: pd.DataFrame, percent_columns: set[str] | None = None) -> str:
    if frame.empty:
        return '<p class="muted">尚未生成。</p>'
    show = frame.copy()
    percent_columns = percent_columns or set()
    for column in show.columns:
        if column in percent_columns:
            show[column] = show[column].map(lambda value: "—" if pd.isna(value) else f"{float(value):.2%}")
        elif pd.api.types.is_float_dtype(show[column]):
            show[column] = show[column].map(lambda value: "—" if pd.isna(value) else f"{float(value):.3f}")
        elif pd.api.types.is_datetime64_any_dtype(show[column]):
            show[column] = show[column].dt.strftime("%Y-%m-%d")
    return show.to_html(index=False, border=0, escape=True, classes="data-table")


def build_report(output_dir: str | Path, annualization: int = 252) -> Path:
    root = Path(output_dir)
    comparison = compare_runs(root, annualization)
    independent = _independent_metrics(root, annualization)
    latest = _latest_holdings(root)
    changes = _holding_changes(root)
    blocked, blocked_summary = _blocked_orders(root)
    stop_summary = _stop_summary(root)
    frequency = _frequency_comparison(root)
    data_quality, data_quality_warning = _data_quality(root)
    regime_summary = _regime_summary(root)
    regime_contributions = _regime_contributions(root)
    yearly_parts, equities = [], {}
    for mode in ("adaptive",):
        frame = pd.read_csv(root / mode / "equity.csv", parse_dates=["date"]).set_index("date")
        equities[mode] = frame
        years = sorted(frame.index.year.unique())
        part = period_metrics(frame["nav"], [(str(y), f"{y}-01-01", f"{y}-12-31") for y in years], annualization)
        part.insert(0, "mode", mode)
        yearly_parts.append(part)
    periods = pd.concat(yearly_parts, ignore_index=True)
    periods.to_csv(root / "period_comparison.csv", index=False)
    contribution_rows = []
    for mode in ("adaptive",):
        contribution = _read_csv(root / mode / "strategy_contributions.csv")
        for sleeve in SLEEVES:
            contribution_rows.append({"mode": mode, "sleeve": sleeve, "sleeve_zh": SLEEVE_LABELS[sleeve],
                                      "cumulative_pnl": float(contribution[sleeve].sum()) if not contribution.empty else 0.0})
    contributions = pd.DataFrame(contribution_rows)
    contributions.to_csv(root / "contribution_summary.csv", index=False)
    allocations = _read_csv(root / "adaptive" / "allocation_history.csv", parse_dates=["date"])
    allocation_display = allocations.tail(30).copy()
    if not allocation_display.empty:
        allocation_display = allocation_display[["date", "regime", "risk_tier", "small_cap", "etf", "blue_chip", "cash",
                                                 "small_cap_count", "etf_count", "blue_chip_count"]]
    adaptive = equities["adaptive"].reset_index()
    adaptive_metrics = comparison.set_index("mode").loc["adaptive"]
    target_gap = float(TARGET_ANNUAL_RETURN - adaptive_metrics["annual_return"])
    start_date, end_date = adaptive["date"].min(), adaptive["date"].max()
    latest_date = end_date
    latest_allocation = _latest_allocation_summary(root, latest, latest_date)
    if not latest_allocation.empty:
        latest_allocation.to_csv(root / "latest_allocation.csv", index=False)
    run_meta = json.loads((root / "adaptive" / "run_summary.json").read_text(encoding="utf-8"))
    config_digest = run_meta.get("config_hash", "未生成")
    latest_columns = ["date", "sleeve_zh", "symbol", "name", "name_asof_signal", "name_current", "name_source", "shares", "close", "market_value", "weight"]
    latest_display = latest[[c for c in latest_columns if c in latest]] if not latest.empty else latest
    latest_allocation_columns = ["date", "asset", "target_budget", "actual_weight", "weight_gap", "market_value"]
    latest_allocation_display = latest_allocation[[c for c in latest_allocation_columns if c in latest_allocation]]
    blocked_columns = ["date", "symbol", "side_zh", "shares", "reason_zh"]
    blocked_display = blocked[[c for c in blocked_columns if c in blocked]] if not blocked.empty else blocked
    benchmark_annual = float(adaptive_metrics.get("benchmark_annual_return", np.nan))
    annual_excess = float(adaptive_metrics.get("annual_excess_return", np.nan))
    target_message = (f"达到 20% 年化目标，实测 {adaptive_metrics['annual_return']:.2%}。" if target_gap <= 0 else
                      f"未达到 20% 年化目标：实测 {adaptive_metrics['annual_return']:.2%}，差 {target_gap:.2%}。未通过改写净值或未来信息补足。")
    independent_note = "；".join(f"{r.strategy_zh} {r.annual_return:.2%}{' ✓' if r.target_20pct_met else ' ✕'}" for r in independent.itertuples()) if not independent.empty else "尚未运行独立子策略。"
    summary = {"benchmark": "CSI 300 price index (000300.SH)", "reference_claim_is_target": False,
               "requested_annual_target": TARGET_ANNUAL_RETURN, "observed_adaptive_annual_return": float(adaptive_metrics["annual_return"]),
               "benchmark_annual_return": benchmark_annual, "annual_excess_return": annual_excess,
               "target_met": bool(adaptive_metrics["target_20pct_met"]), "data_end": end_date.strftime("%Y-%m-%d"),
               "config_hash": config_digest,
               "data_quality_ok": bool(data_quality.get("ok", False)),
               "data_quality_artifact": str((root.parents[2] / "data" / "cache_pandadata_20260918" / "data_dictionary.json").resolve()),
               "comparison": json.loads(comparison.to_json(orient="records")), "independent_strategies": json.loads(independent.to_json(orient="records")),
               "limitations": [f"缓存行情截至 {end_date:%Y-%m-%d}，并非当前自然日。", "本次是 JoinQuant 代码的日线近似复刻；原代码未启用审计意见门槛，不能把复刻结果解释为审计合规筛选。增强严格模式的真实审计意见主要从2023年开始。",
                               "复权价格未建立原始股本与现金分红的逐笔公司行动账本。", f"{data_quality_warning}，verified前需逐项复核。"]}
    (root / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>市场风格自适应策略切换 Agent</title>
<style>:root{{--ink:#16221b;--muted:#66736b;--paper:#f4f1e8;--card:#fffdf7;--line:#d8d3c5;--green:#174d35;--lime:#b9d96c;--orange:#d96f32}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 Inter,"PingFang SC","Microsoft YaHei",sans-serif}}.shell{{max-width:1280px;margin:auto;padding:28px}}header{{padding:42px;background:var(--green);color:#fff;border-radius:22px}}.eyebrow{{letter-spacing:.18em;text-transform:uppercase;color:var(--lime);font-size:12px}}h1{{font-size:clamp(32px,5vw,64px);line-height:1.05;margin:10px 0 18px;max-width:900px}}h2{{font-size:25px;margin:0 0 14px}}.lede{{max-width:850px;color:#dce9df;font-size:17px}}.grid{{display:grid;grid-template-columns:repeat(12,1fr);gap:16px;margin:18px 0}}.card{{grid-column:span 4;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px;overflow:auto}}.wide{{grid-column:1/-1}}.half{{grid-column:span 6}}.metric{{font:700 32px/1.1 ui-monospace,SFMono-Regular,Consolas,monospace}}.label,.muted{{color:var(--muted)}}.status{{display:inline-block;border-radius:999px;padding:5px 10px;background:#efe1d8;color:#8d3e1b;font-weight:700}}.status.ok{{background:#dfebc2;color:#274d18}}.flow{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.step{{padding:14px;border-top:3px solid var(--green);background:#edf0e7}}.data-table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}th{{position:sticky;top:0;background:#eeeadd}}svg{{width:100%;height:auto;background:#fffdf7}}details{{border-top:1px solid var(--line);padding:10px 0}}summary{{cursor:pointer;font-weight:700}}code{{background:#eeeadd;padding:2px 5px;border-radius:4px}}@media(max-width:800px){{.card,.half{{grid-column:1/-1}}.flow{{grid-template-columns:1fr 1fr}}.shell{{padding:14px}}}}</style></head>
<body><main class="shell"><header><div class="eyebrow">Market Style Adaptive Agent · Point-in-time research</div><h1>市场风格自适应策略切换 Agent</h1><p class="lede">样本 {start_date:%Y-%m-%d}—{end_date:%Y-%m-%d}。市场状态 skill 给出 overheated / warm / cold / crisis，三个子策略 skill 独立选券，组合风险层决定预算、现金与下一交易日执行。</p></header><section class="grid">
<article class="card"><div class="label">自适应年化</div><div class="metric">{adaptive_metrics['annual_return']:.2%}</div></article><article class="card"><div class="label">对沪深300年化超额</div><div class="metric">{annual_excess:.2%}</div><div class="muted">策略 {adaptive_metrics['annual_return']:.2%} · 沪深300 {benchmark_annual:.2%}</div></article><article class="card"><div class="label">最大回撤</div><div class="metric">{adaptive_metrics['max_drawdown']:.2%}</div><div class="muted">发生于 {adaptive_metrics['max_drawdown_date']}</div></article><article class="card"><div class="label">最新持仓日</div><div class="metric">{latest_date:%Y-%m-%d}</div><div class="muted">缓存最新，不等同自然日今天</div></article>
<article class="card wide"><h2>Agent 联动链</h2><div class="flow"><div class="step"><b>01 状态 skill</b><br>趋势 + 长均线 + 扩展波动分位</div><div class="step"><b>02 子策略 skill</b><br>各自点时筛选与排序</div><div class="step"><b>03 内部主动轮动</b><br>名义大类预算 40%/40%/20%；状态驱动袖套内部集中度、阈值与标的</div><div class="step"><b>04 执行与证据</b><br>止损即时卖出；T+1 / 涨跌停 / 停牌 / 成本 / 台账</div></div></article>
<article class="card wide"><h2>净值路径</h2>{_equity_svg(adaptive)}</article><article class="card wide"><h2>自适应 Agent 绩效</h2>{_fmt_table(comparison, {'total_return','annual_return','annual_volatility','max_drawdown','benchmark_total_return','benchmark_annual_return','excess_return','annual_excess_return'})}</article>
<article class="card wide"><h2>换仓频率选择</h2><p class="muted">周频与半月频使用同一份 PandaData 缓存、同一套小市值/白马配置和冻结 ETF 参数；按自适应组合全区间年化选择周频。止损仍按每日收盘信号、下一交易日开盘执行。</p>{_fmt_table(frequency, {'total_return','annual_return','annual_volatility','max_drawdown','benchmark_total_return','benchmark_annual_return','excess_return','annual_excess_return'})}</article>
<article class="card wide"><h2>三个子策略独立回测</h2><p>{escape(independent_note)}</p>{_fmt_table(independent, {'total_return','annual_return','annual_volatility','max_drawdown','benchmark_total_return','benchmark_annual_return','excess_return','annual_excess_return'})}</article>
<article class="card wide"><h2>最新一期真实持仓</h2><p class="muted">按回测最后交易日 {latest_date:%Y-%m-%d} 收盘市值；若止损后无股票/ETF，持仓表为空且现金行会显示 100%，不会回显上一交易日持仓。名称优先采用信号日 stock_daily 快照；name_current 仅供当前导航，不能用于历史合规判断。</p>{_fmt_table(latest_display, {'weight'})}<h3>名义预算 vs 实际资产分布</h3><p class="muted">目标预算来自 allocation_history；实际权重按净值与持仓市值重算。差额为实际权重减目标预算。</p>{_fmt_table(latest_allocation_display, {'target_budget','actual_weight','weight_gap'})}</article>
<article class="card wide"><h2>每次持仓变化</h2><p class="muted">完整表保存为 <code>holding_changes.csv</code>。</p><details open><summary>展开全部 {len(changes)} 次成交日</summary>{_fmt_table(changes)}</details></article>
<article class="card wide"><h2>止损触发统计</h2><p class="muted">止损信号按收盘触发、下一交易日开盘执行；ETF 参数沿用冻结配置。完整成交原因保存在 <code>trades.csv</code>，信号证据保存在 <code>selection_evidence.csv</code>。</p>{_fmt_table(stop_summary)}</article>
<article class="card wide"><h2>未成交订单与交易约束</h2><p class="muted">以下订单曾被策略提出但未成交，因此不计入成交、换手或交易成本；完整明细保存为 <code>blocked_orders.csv</code>，报告副本为 <code>blocked_orders_report.csv</code>。它们解释了部分时期实际持仓与名义预算的偏差。</p>{_fmt_table(blocked_summary)}<details><summary>展开全部 {len(blocked)} 笔未成交订单</summary>{_fmt_table(blocked_display)}</details></article>
<article class="card half"><h2>市场状态条件表现</h2>{_fmt_table(regime_summary, {'share','avg_daily_return','annualized_volatility','average_cash'})}</article><article class="card half"><h2>策略贡献</h2>{_fmt_table(contributions)}</article>
<article class="card wide"><h2>按市场状态归因</h2><p class="muted">各状态中三个 sleeve 的累计日度损益，直接体现状态 skill 对子策略预算和结果的联动。</p>{_fmt_table(regime_contributions)}</article>
<article class="card wide"><h2>最近 30 次预算与内部轮动</h2><p class="muted">allocation_history 中的小市值40%、ETF40%、白马20%是名义 sleeve 预算，不等于每个交易日的实际投资权重。风险层、合格标的缺失、止损和未成交订单可能使预算转为现金；表中的持股数量和状态变化记录内部换股与集中度。</p>{_fmt_table(allocation_display, {'small_cap','etf','blue_chip','cash'})}</article><article class="card wide"><h2>逐年表现</h2>{_fmt_table(periods, {'total_return','annual_return','annual_volatility','max_drawdown'})}</article>
<article class="card wide"><h2>研究边界</h2><ul><li>研究与教育用途，不连接券商、不构成投资建议、不承诺收益。</li><li>原始收益数字仅为参考陈述，不是已复现事实。</li><li>本次按全区间年化选优，存在数据挖掘和过拟合风险；分段结果仅作诊断，不能替代独立样本验证。</li><li>配置 hash：<code>{escape(config_digest)}</code>；数据 manifest 与交易台账保存在本次输出目录。</li><li>数据质量校验：{escape(str(data_quality.get('ok', False)))}；校验文件来自 PandaData 缓存 data_dictionary.json。</li><li>行情缓存截至 {end_date:%Y-%m-%d}；“至今”仅指同源数据共同覆盖的最新交易日。</li><li>本次为 JoinQuant 代码的日线近似复刻：收盘信号、下一交易日开盘执行；原代码未启用审计意见门槛。若切换严格增强模式，真实审计意见主要从 2023 年开始。</li><li>{escape(data_quality_warning)}，当前保持 runnable。</li><li>20% 是目标门槛，不是可篡改的验收结果；全部指标由成交台账与净值重算。</li></ul></article></section></main></body></html>"""
    path = root / "report.html"
    path.write_text(report, encoding="utf-8")
    markdown = ("# 市场风格自适应策略结构性回测\n\n" f"- 样本：{start_date:%Y-%m-%d} 至 {end_date:%Y-%m-%d}\n- 20% 年化目标：{target_message}\n" f"- 子策略：{independent_note}\n- 最新持仓：`latest_holdings.csv`\n- 全部持仓变化：`holding_changes.csv`\n\n" "## 自适应策略绩效\n\n" + comparison.to_markdown(index=False) + f"\n\n## 研究边界\n\n- 研究与教育用途，不承诺收益。\n- 缓存截至 {end_date:%Y-%m-%d}，并非当前自然日。\n- 审计意见历史与复权跳变限制详见 HTML。\n")
    (root / "handoff_report.md").write_text(markdown, encoding="utf-8")
    return path


def _equity_svg(adaptive: pd.DataFrame) -> str:
    width, height, pad = 1180, 360, 42
    series = {"自适应": adaptive["nav"] / adaptive["nav"].iloc[0], "沪深300": adaptive["benchmark"] / adaptive["benchmark"].iloc[0]}
    all_values = pd.concat(list(series.values())); low, high = float(all_values.min()), float(all_values.max()); span = high - low or 1.0
    colors = {"自适应": "#174d35", "沪深300": "#d96f32"}; lines = []
    for label, values in series.items():
        points = []
        for i, value in enumerate(values):
            x = pad + i / max(len(values) - 1, 1) * (width - 2 * pad); y = height - pad - (float(value) - low) / span * (height - 2 * pad)
            points.append(f"{x:.1f},{y:.1f}")
        lines.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{colors[label]}" stroke-width="2"/>')
    legend = "".join(f'<text x="{pad + i * 120}" y="22" fill="{colors[label]}">{label}</text>' for i, label in enumerate(series))
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="策略与基准净值">{legend}{"".join(lines)}</svg>'



