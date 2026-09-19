"""Small-cap/blue-chip research grid; ETF configuration is never changed."""
from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

import pandas as pd
import yaml

from agent_market_style_adaptive.backtest import Backtester
from agent_market_style_adaptive.config import config_hash
from agent_market_style_adaptive.metrics import performance_metrics


def candidate_metrics(result, annualization=252):
    frame = result.equity.set_index("date")
    metric = performance_metrics(frame["nav"], frame["benchmark"], annualization)
    metric["trades"] = len(result.trades)
    metric["cost"] = float(result.trades["cost"].sum()) if not result.trades.empty else 0.0
    metric["config_hash"] = config_hash(result.metadata.get("config", {})) if result.metadata.get("config") else ""
    return metric


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/optimized.rebalance.yaml")
    ap.add_argument("--output", default="outputs/stock_sleeve_search")
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as f:
        base = yaml.safe_load(f)
    base["run"]["active_sleeves"] = ["small_cap"]
    base["allocation"]["static"] = {"small_cap": 1.0, "etf": 0.0, "blue_chip": 0.0, "cash": 0.0}
    base["risk"]["warning_drawdown"] = 2.0
    base["risk"]["crisis_drawdown"] = 3.0
    base["sleeve_risk_budget"] = {"method": "absolute_target_vol"}
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    engine = Backtester.from_cache(base)
    rows = []
    small_candidates = []
    for audit_mode in ("strict", "proxy_or_strict"):
        for dividend in (True, False):
            for cap_max in (10e9, 15e9, 30e9):
                for skip in (True, False):
                    for freq in ("weekly", "semi_monthly"):
                        cfg = copy.deepcopy(base)
                        cfg["small_cap"].update({"audit_mode": audit_mode, "require_recent_dividend": dividend,
                                                   "market_cap_max": cap_max, "skip_months": [1,4] if skip else []})
                        cfg["rebalance"] = {"frequency": freq, "daily_stops_enabled": True}
                        engine.config = cfg; engine._selection_cache = {}
                        t0=time.time(); res=engine.run("static"); elapsed=time.time()-t0
                        frame=res.equity.set_index("date")
                        m=performance_metrics(frame["nav"],frame["benchmark"],252)
                        rows.append({"sleeve":"small_cap","audit_mode":audit_mode,"require_recent_dividend":dividend,"cap_max":cap_max,"skip_months":skip,"frequency":freq,"elapsed":elapsed,"trades":len(res.trades),"cost":float(res.trades["cost"].sum()) if not res.trades.empty else 0.0,**m})
                        print(rows[-1], flush=True)
    # blue-chip grid: restore one-sleeve capital and only vary blue-chip fields.
    base["run"]["active_sleeves"] = ["blue_chip"]
    base["allocation"]["static"] = {"small_cap": 0.0, "etf": 0.0, "blue_chip": 1.0, "cash": 0.0}
    for cap_max in (6e9, 30e9, 100e9, None):
        for pb_max in (6.0, 10.0):
            for lvq in (0.5,0.6,0.7):
                for price_max in (50.0,100.0):
                    for freq in ("weekly","semi_monthly"):
                        cfg=copy.deepcopy(base)
                        updates={"pb_max":pb_max,"low_vol_quantile":lvq,"price_max":price_max}
                        if cap_max is None: cfg["blue_chip"].pop("market_cap_max",None)
                        else: updates["market_cap_max"]=cap_max
                        cfg["blue_chip"].update(updates); cfg["rebalance"]={"frequency":freq,"daily_stops_enabled":True}
                        engine.config=cfg; engine._selection_cache={}
                        t0=time.time(); res=engine.run("static"); elapsed=time.time()-t0
                        frame=res.equity.set_index("date"); m=performance_metrics(frame["nav"],frame["benchmark"],252)
                        rows.append({"sleeve":"blue_chip","cap_max":cap_max,"pb_max":pb_max,"low_vol_quantile":lvq,"price_max":price_max,"frequency":freq,"elapsed":elapsed,"trades":len(res.trades),"cost":float(res.trades["cost"].sum()) if not res.trades.empty else 0.0,**m})
                        print(rows[-1], flush=True)
    frame=pd.DataFrame(rows)
    frame.to_csv(out/"leaderboard.csv",index=False)
    (out/"leaderboard.json").write_text(frame.to_json(orient="records",force_ascii=False,indent=2),encoding="utf-8")
    print(frame.sort_values(["sleeve","annual_return"],ascending=[True,False]).groupby("sleeve").head(10).to_string(index=False))


if __name__ == "__main__": main()
