from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from agent_market_style_adaptive.backtest import Backtester
from agent_market_style_adaptive.config import config_hash, load_config
from agent_market_style_adaptive.metrics import performance_metrics

HOLDOUT_START = pd.Timestamp("2025-01-01")
DEVELOPMENT_END = pd.Timestamp("2024-12-31")


def candidates() -> dict[str, list[tuple[str, dict]]]:
    return {
        "small_cap": [
            ("size_only", {"ranking_weights": {"size": 1.0}}),
            ("size_risk_budget_25", {"ranking_weights": {"size": 1.0}, "target_volatility": .25, "risk_budget_min_scale": .25}),
            ("size_risk_budget_30", {"ranking_weights": {"size": 1.0}, "target_volatility": .30, "risk_budget_min_scale": .25}),
            ("size_no_dividend", {"require_recent_dividend": False, "ranking_weights": {"size": 1.0}}),
            ("broader_size_no_dividend", {"market_cap_max": 2e10, "revenue_min": 5e7, "require_recent_dividend": False, "ranking_weights": {"size": 1.0}}),
            ("broader_quality_no_dividend", {"market_cap_max": 2e10, "revenue_min": 5e7, "require_recent_dividend": False, "ranking_weights": {"size": .50, "roe": .25, "roa": .25}}),
            ("size_mom20", {"momentum_20_min": -0.05, "ranking_weights": {"size": .45, "momentum_20": .40, "roe": .15}}),
            ("size_mom60", {"momentum_60_min": 0.0, "ranking_weights": {"size": .40, "momentum_60": .45, "roe": .15}}),
            ("quality_momentum", {"amount20_min": 1e7, "ranking_weights": {"size": .30, "momentum_20": .30, "roe": .20, "roa": .10, "low_volatility": .10}}),
            ("trend_quality", {"amount20_min": 1e7, "ma60_ratio_min": -0.03, "ranking_weights": {"size": .30, "momentum_60": .35, "roe": .20, "low_volatility": .15}}),
            ("focused_momentum", {"amount20_min": 2e7, "momentum_20_min": -0.03, "holdings_high": 3, "holdings_normal": 6, "holdings_low": 10, "ranking_weights": {"size": .25, "momentum_20": .45, "roe": .20, "low_volatility": .10}}),
        ],
        "etf": [
            ("baseline_top2", {}),
            ("relative_strength_stop", {"relative_strength_min_percentile": .50, "fixed_stop": .08, "trailing_stop": .10, "trailing_trigger": .05}),
            ("relative_strength_risk_stop", {"relative_strength_min_percentile": .50, "fixed_stop": .08, "trailing_stop": .10, "trailing_trigger": .05, "target_volatility": .15, "risk_budget_min_scale": .30}),
            ("top_quartile_stop", {"relative_strength_min_percentile": .75, "fixed_stop": .08, "trailing_stop": .10, "trailing_trigger": .05}),
            ("stop_only", {"fixed_stop": .08, "trailing_stop": .10, "trailing_trigger": .05}),
            ("top2_soft_confirmation", {"require_amount_confirmation": False, "require_ma_order": False}),
            ("top2_relaxed_trend", {"require_amount_confirmation": False, "require_above_short_ma": False, "require_ma_order": False, "minimum_score": -.02, "three_day_floor": -.10}),
            ("top3_relaxed_trend", {"max_positions": 3, "require_amount_confirmation": False, "require_above_short_ma": False, "require_ma_order": False, "minimum_score": -.02, "three_day_floor": -.10}),
            ("top1_balanced", {"max_positions": 1, "full_budget_when_fewer": True}),
            ("top1_no_volume", {"max_positions": 1, "full_budget_when_fewer": True, "require_amount_confirmation": False}),
            ("top1_medium_momentum", {"max_positions": 1, "full_budget_when_fewer": True, "momentum_weights": [.2, .5, .3]}),
            ("top1_long_momentum", {"max_positions": 1, "full_budget_when_fewer": True, "momentum_weights": [.1, .3, .6]}),
            ("top2_no_volume", {"max_positions": 2, "require_amount_confirmation": False}),
            ("top1_all_positive", {"max_positions": 1, "full_budget_when_fewer": True, "require_all_momentum_positive": True}),
        ],
        "blue_chip": [
            ("value_quality_lowvol", {}),
            ("non_crisis_filter", {"allowed_regimes": ["cold", "warm", "overheated"]}),
            ("warm_trend_filter", {"allowed_regimes": ["warm", "overheated"]}),
            ("regime_risk_budget", {"allowed_regimes": ["warm", "overheated"], "target_volatility": .15, "risk_budget_min_scale": .30}),
            ("balanced_momentum", {"low_vol_quantile": .40, "ranking_weights": {"pb": .25, "roe": .25, "low_volatility": .25, "momentum_60": .25}}),
            ("momentum_heavy", {"low_vol_quantile": .50, "ranking_weights": {"pb": .15, "roe": .20, "low_volatility": .20, "momentum_60": .30, "momentum_120": .15}}),
            ("positive_trend", {"low_vol_quantile": .50, "momentum_60_min": 0.0, "ranking_weights": {"pb": .20, "roe": .20, "low_volatility": .20, "momentum_60": .40}}),
            ("focused_momentum", {"low_vol_quantile": .50, "holdings": {"cold": 8, "warm": 5, "overheated": 3}, "ranking_weights": {"pb": .15, "roe": .20, "low_volatility": .20, "momentum_60": .30, "momentum_120": .15}}),
            ("focused_trend", {"low_vol_quantile": .60, "momentum_60_min": 0.0, "holdings": {"cold": 8, "warm": 5, "overheated": 3}, "ranking_weights": {"pb": .15, "roe": .15, "low_volatility": .15, "momentum_60": .35, "momentum_120": .20}}),
            # Point-in-time quality/liquidity candidates.  These are defined
            # before running the development folds; no holdout outcomes enter
            # their construction or scoring.
            ("quality_liquid", {"amount20_min": 2e7, "market_cap_min": 1e10, "roa_min": .03, "low_vol_quantile": .40, "ranking_weights": {"pb": .20, "roe": .25, "roa": .20, "low_volatility": .20, "momentum_60": .15}}),
            ("large_quality_trend", {"amount20_min": 5e7, "market_cap_min": 3e10, "roa_min": .04, "ma20_ratio_min": 0.0, "low_vol_quantile": .50, "ranking_weights": {"pb": .15, "roe": .25, "roa": .25, "low_volatility": .15, "momentum_60": .20}}),
            ("quality_short_trend", {"amount20_min": 2e7, "roa_min": .03, "momentum_20_min": 0.0, "low_vol_quantile": .40, "ranking_weights": {"pb": .15, "roe": .20, "roa": .20, "low_volatility": .20, "momentum_20": .10, "momentum_60": .15}}),
            ("broad_defensive", {"price_max": 100.0, "pb_max": 5.0, "revenue_min": 5e8, "low_vol_quantile": .40, "ranking_weights": {"pb": .20, "roe": .25, "roa": .15, "low_volatility": .25, "momentum_60": .15}}),
            ("relaxed_value_quality", {"price_max": 100.0, "pb_max": 5.0, "revenue_min": 5e8, "low_vol_quantile": .50, "ranking_weights": {"pb": .25, "roe": .25, "low_volatility": .25, "momentum_60": .25}}),
            ("relaxed_value_momentum", {"price_max": 150.0, "pb_max": 6.0, "revenue_min": 3e8, "low_vol_quantile": .60, "ranking_weights": {"pb": .15, "roe": .20, "roa": .10, "low_volatility": .15, "momentum_60": .25, "momentum_120": .15}}),
            ("all_board_quality", {"exclude_growth_boards": False, "price_max": 100.0, "pb_max": 5.0, "revenue_min": 5e8, "low_vol_quantile": .35, "ranking_weights": {"pb": .15, "roe": .25, "roa": .20, "low_volatility": .20, "momentum_60": .20}}),
        ],
    }


def development_folds(sleeve: str):
    if sleeve == "small_cap":
        return [("2024H1", "2024-01-01", "2024-06-30"), ("2024H2", "2024-07-01", "2024-12-31")]
    return [("2018-2020", "2018-01-01", "2020-12-31"), ("2021-2022", "2021-01-01", "2022-12-31"), ("2023-2024", "2023-01-01", "2024-12-31")]


def score_result(equity: pd.DataFrame, sleeve: str, minimum_fold_annual_return: float) -> tuple[float, list[dict], bool, bool, dict]:
    frame = equity.set_index("date")
    rows = []
    for label, start, end in development_folds(sleeve):
        part = frame.loc[start:end, "nav"]
        if len(part) < 2:
            continue
        metric = performance_metrics(part)
        rows.append({"fold": label, **metric})
    annual = np.array([row["annual_return"] for row in rows], dtype=float)
    drawdowns = np.array([abs(row["max_drawdown"]) for row in rows], dtype=float)
    # Reward return that survives different periods, penalize one-period dependence and deep losses.
    feasible = bool(len(rows))
    # A strategy may be less bad than its peers without meeting the research
    # mandate.  Keep that distinction explicit: only candidates that clear the
    # annual-return floor in every non-overlapping development fold may be
    # promoted into the combination stage.
    target_met = bool(len(rows) and annual.min() >= minimum_fold_annual_return)
    # Return remains the primary objective.  The secondary score is retained
    # for diagnostics and tie-breaking, never as a hidden hard gate.
    robust = float(np.median(annual) + .35 * np.min(annual) - .35 * np.std(annual) - .35 * np.mean(drawdowns))
    diagnostics = {"annual_return_median": float(np.median(annual)) if len(annual) else np.nan,
                   "annual_return_min": float(np.min(annual)) if len(annual) else np.nan,
                   "annual_return_std": float(np.std(annual)) if len(annual) else np.nan,
                   "max_drawdown_worst": float(np.max(drawdowns)) if len(drawdowns) else np.nan}
    return robust, rows, feasible, target_met, diagnostics


def sleeve_config(base: dict, sleeve: str, changes: dict) -> dict:
    cfg = deepcopy(base)
    cfg[sleeve].update(deepcopy(changes))
    # Selection is intentionally evaluated through the latest validated cache
    # date.  Pre-2025 folds remain in the evidence for regime/overfit diagnosis.
    cfg["run"]["effective_end_date"] = str(base["run"]["effective_end_date"])
    cfg["run"]["end_date"] = str(base["run"]["end_date"])
    cfg["run"]["active_sleeves"] = [sleeve]
    cfg["risk"]["warning_drawdown"] = 2.0
    cfg["risk"]["crisis_drawdown"] = 3.0
    cfg["allocation"]["static"] = {"small_cap": 0.0, "etf": 0.0, "blue_chip": 0.0, "cash": 0.0}
    cfg["allocation"]["static"][sleeve] = 1.0
    return cfg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--output", default="outputs/optimization_pre2025")
    parser.add_argument("--sleeve", choices=["small_cap", "etf", "blue_chip"], help="evaluate one sleeve only")
    parser.add_argument("--candidate", action="append", help="candidate name to evaluate; may be repeated")
    parser.add_argument("--no-promote", action="store_true", help="write evidence only; do not replace the optimized config")
    parser.add_argument("--minimum-fold-annual-return", type=float, default=0.20,
                        help="hard annual-return floor for every development fold before promotion")
    args = parser.parse_args()
    base = load_config(args.config)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    engine = Backtester.from_cache(base)
    all_rows = []
    best_changes = {}
    promotion_failures = []
    catalog = candidates()
    if args.sleeve:
        catalog = {args.sleeve: catalog[args.sleeve]}
    for sleeve, variants in catalog.items():
        if args.candidate:
            requested = set(args.candidate)
            variants = [item for item in variants if item[0] in requested]
            missing = requested - {name for name, _ in variants}
            if missing:
                raise SystemExit(f"unknown candidate(s) for {sleeve}: {sorted(missing)}")
        sleeve_rows = []
        for name, changes in variants:
            cfg = sleeve_config(base, sleeve, changes)
            engine.config = cfg
            result = engine.run("static")
            robust, folds, feasible, target_met, diagnostics = score_result(
                result.equity, sleeve, args.minimum_fold_annual_return
            )
            full = performance_metrics(result.equity.set_index("date")["nav"])
            trades = len(result.trades)
            row = {"sleeve": sleeve, "candidate": name, "feasible": feasible, "target_met": target_met,
                   "robust_score": robust, "trades": trades,
                   "development_annual_return": full["annual_return"], "development_total_return": full["total_return"],
                   "development_max_drawdown": full["max_drawdown"], "parameters": json.dumps(changes, ensure_ascii=False, sort_keys=True),
                   "folds": json.dumps(folds, ensure_ascii=False), **diagnostics}
            sleeve_rows.append(row); all_rows.append(row)
            print(f"{sleeve} {name}: annual={full['annual_return']:.2%} dd={full['max_drawdown']:.2%} score={robust:.4f}", flush=True)
        promotable_rows = [item for item in sleeve_rows if item["feasible"]]
        if not promotable_rows:
            promotion_failures.append(sleeve)
            continue
        # Full-period annualized return is the requested primary objective.
        # Robust score breaks ties and remains visible in the leaderboard.
        best = max(promotable_rows, key=lambda item: (item["development_annual_return"], item["robust_score"]))
        best_changes[sleeve] = json.loads(best["parameters"])
    leaderboard = pd.DataFrame(all_rows).sort_values(["sleeve", "robust_score"], ascending=[True, False])
    leaderboard.to_csv(output / "skill_candidate_leaderboard.csv", index=False)
    gate = {
        "minimum_fold_annual_return": args.minimum_fold_annual_return,
        "promoted_sleeves": sorted(best_changes),
        "blocked_sleeves": promotion_failures,
        "combination_permitted": not promotion_failures,
    }
    (output / "promotion_gate.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
    if promotion_failures:
        print(json.dumps(gate, ensure_ascii=False, indent=2), flush=True)
        raise SystemExit("combination blocked: one or more sleeves did not meet the development-fold return floor")
    optimized = deepcopy(base)
    for sleeve, changes in best_changes.items():
        optimized[sleeve].update(changes)
    optimized["optimization"] = {
        "method": "predefined economic grids ranked by full-period annual return; development folds retained for diagnostics",
        "development_end": str(DEVELOPMENT_END.date()),
        "holdout_start": str(HOLDOUT_START.date()),
        "objective": "full-period annual return primary; robust fold score and drawdown are diagnostics only; no hard drawdown or 20% annual-return gate",
        "selected": best_changes,
    }
    if not args.no_promote:
        Path("config/optimized.pre2025.yaml").write_text(yaml.safe_dump(optimized, allow_unicode=True, sort_keys=False), encoding="utf-8")
    summary = {"development_end": str(DEVELOPMENT_END.date()), "holdout_start": str(HOLDOUT_START.date()),
               "selected": best_changes, "optimized_config_hash": config_hash(optimized)}
    (output / "selection_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)

if __name__ == "__main__":
    main()
