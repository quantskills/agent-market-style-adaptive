from __future__ import annotations

import numpy as np
import pandas as pd


def performance_metrics(equity: pd.Series, benchmark: pd.Series | None = None, annualization: int = 252) -> dict[str, float | str]:
    series = equity.dropna().astype(float)
    if len(series) < 2:
        raise ValueError("at least two equity observations are required")
    returns = series.pct_change(fill_method=None).dropna()
    if isinstance(series.index, pd.DatetimeIndex):
        elapsed_days = (series.index[-1] - series.index[0]).total_seconds() / 86400
        years = elapsed_days / 365.2425
    else:
        years = len(returns) / annualization
    total_return = float(series.iloc[-1] / series.iloc[0] - 1)
    annual_return = float((series.iloc[-1] / series.iloc[0]) ** (1 / years) - 1) if years > 0 else np.nan
    volatility = float(returns.std(ddof=1) * np.sqrt(annualization))
    sharpe = float(returns.mean() / returns.std(ddof=1) * np.sqrt(annualization)) if returns.std(ddof=1) > 0 else np.nan
    downside = float(np.sqrt(np.mean(np.minimum(returns.to_numpy(), 0.0) ** 2)))
    sortino = float(returns.mean() / downside * np.sqrt(annualization)) if downside > 0 else np.nan
    drawdown = series / series.cummax() - 1
    max_drawdown = float(drawdown.min())
    drawdown_date = drawdown.idxmin()
    calmar = float(annual_return / abs(max_drawdown)) if max_drawdown < 0 else np.nan
    result: dict[str, float | str] = {
        "total_return": total_return, "annual_return": annual_return, "annual_volatility": volatility,
        "sharpe": sharpe, "sortino": sortino, "max_drawdown": max_drawdown,
        "max_drawdown_date": pd.Timestamp(drawdown_date).strftime("%Y-%m-%d"), "calmar": calmar,
    }
    if benchmark is not None:
        aligned = pd.concat([returns.rename("strategy"), benchmark.pct_change(fill_method=None).rename("benchmark")], axis=1, sort=False).dropna()
        if len(aligned) > 1:
            benchmark_level = benchmark.reindex(series.index).dropna()
            benchmark_total = float(benchmark_level.iloc[-1] / benchmark_level.iloc[0] - 1)
            benchmark_years = ((benchmark_level.index[-1] - benchmark_level.index[0]).total_seconds() / 86400) / 365.2425 if isinstance(benchmark_level.index, pd.DatetimeIndex) else years
            benchmark_annual = float((benchmark_level.iloc[-1] / benchmark_level.iloc[0]) ** (1 / benchmark_years) - 1) if benchmark_years > 0 else np.nan
            covariance = aligned.cov()
            beta = float(covariance.loc["strategy", "benchmark"] / covariance.loc["benchmark", "benchmark"]) if covariance.loc["benchmark", "benchmark"] > 0 else np.nan
            active = aligned["strategy"] - aligned["benchmark"]
            information = float(active.mean() / active.std(ddof=1) * np.sqrt(annualization)) if active.std(ddof=1) > 0 else np.nan
            result.update({"benchmark_total_return": benchmark_total, "benchmark_annual_return": benchmark_annual,
                           "excess_return": total_return - benchmark_total,
                           "annual_excess_return": annual_return - benchmark_annual,
                           "beta": beta, "information_ratio": information})
    return result


def period_metrics(equity: pd.Series, boundaries: list[tuple[str, str, str]], annualization: int = 252) -> pd.DataFrame:
    rows = []
    for label, start, end in boundaries:
        part = equity.loc[pd.Timestamp(start):pd.Timestamp(end)]
        if len(part) >= 2:
            rows.append({"period": label} | performance_metrics(part, annualization=annualization))
    return pd.DataFrame(rows)
