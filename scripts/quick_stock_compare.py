from __future__ import annotations
import copy, time, yaml, pandas as pd
from agent_market_style_adaptive.backtest import Backtester
from agent_market_style_adaptive.metrics import performance_metrics

base = yaml.safe_load(open('config/optimized.rebalance.yaml', encoding='utf-8'))
engine = Backtester.from_cache(base)
rows = []

def run(label, cfg):
    engine.config = cfg
    engine._selection_cache = {}
    t = time.time()
    result = engine.run('static')
    elapsed = time.time() - t
    frame = result.equity.set_index('date')
    metrics = performance_metrics(frame.nav, frame.benchmark, 252)
    row = {'label': label, 'elapsed': elapsed, 'trades': len(result.trades),
           'cost': float(result.trades.cost.sum()) if not result.trades.empty else 0.0, **metrics}
    rows.append(row)
    print(row, flush=True)

for frequency in ('weekly', 'semi_monthly'):
    cfg = copy.deepcopy(base)
    cfg['run']['active_sleeves'] = ['small_cap']
    cfg['allocation']['static'] = {'small_cap': 1.0, 'etf': 0.0, 'blue_chip': 0.0, 'cash': 0.0}
    cfg['risk']['warning_drawdown'] = 2.0
    cfg['risk']['crisis_drawdown'] = 3.0
    cfg['sleeve_risk_budget'] = {'method': 'absolute_target_vol'}
    cfg['rebalance'] = {'frequency': frequency, 'daily_stops_enabled': True}
    cfg['small_cap'].update({'audit_mode': 'proxy_or_strict', 'require_recent_dividend': False,
                             'market_cap_max': 10e9, 'skip_months': [1, 4]})
    run('small_proxy_nodiv_' + frequency, cfg)

for frequency in ('weekly', 'semi_monthly'):
    cfg = copy.deepcopy(base)
    cfg['run']['active_sleeves'] = ['blue_chip']
    cfg['allocation']['static'] = {'small_cap': 0.0, 'etf': 0.0, 'blue_chip': 1.0, 'cash': 0.0}
    cfg['risk']['warning_drawdown'] = 2.0
    cfg['risk']['crisis_drawdown'] = 3.0
    cfg['sleeve_risk_budget'] = {'method': 'absolute_target_vol'}
    cfg['rebalance'] = {'frequency': frequency, 'daily_stops_enabled': True}
    cfg['blue_chip'].pop('market_cap_max', None)
    cfg['blue_chip'].update({'price_max': 50.0, 'holdings': {'cold': 5, 'warm': 5, 'overheated': 5}})
    for state in ('cold', 'warm', 'overheated'):
        cfg['blue_chip']['regime_thresholds'][state]['holdings'] = {'cold': 5, 'warm': 5, 'overheated': 5}
    run('blue_nomax_5_' + frequency, cfg)

# Reference white-horse ranking: the JoinQuant code orders by ROA/PB (cold and
# warm) or ROA (hot).  This is evaluated separately so the ETF sleeve is never
# tuned as part of the comparison.
for frequency in ('weekly', 'semi_monthly'):
    cfg = copy.deepcopy(base)
    cfg['run']['active_sleeves'] = ['blue_chip']
    cfg['allocation']['static'] = {'small_cap': 0.0, 'etf': 0.0, 'blue_chip': 1.0, 'cash': 0.0}
    cfg['rebalance'] = {'frequency': frequency, 'daily_stops_enabled': True}
    cfg['blue_chip'].pop('market_cap_max', None)
    cfg['blue_chip'].update({'price_max': 50.0, 'holdings': {'cold': 5, 'warm': 5, 'overheated': 5},
                             'ranking_weights': {'pb': 0.45, 'roe': 0.15, 'roa': 0.30, 'low_volatility': 0.10}})
    for state in ('cold', 'warm', 'overheated'):
        cfg['blue_chip']['regime_thresholds'][state]['holdings'] = {'cold': 5, 'warm': 5, 'overheated': 5}
    run('blue_reference_rank_' + frequency, cfg)

pd.DataFrame(rows).to_csv('outputs/stock_sleeve_search/quick.csv', index=False)
