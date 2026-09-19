# 代码实现说明

核心入口是 `select_small_cap`，位于 `src/qs_small_cap_fundamental/strategy.py`（本 Skill 包内）。独立入口位于 `examples/run_small_cap.py`。

1. `point_in_time_latest` 只保留 `announcement_date < signal_date` 的每股最新财务记录。
2. 市值按当日未复权收盘价乘总股本计算；ROA 按净利润除以总资产计算。
3. 财务、最近年度审计、三年现金分红和可交易性四组条件必须同时通过。
4. `holding_count_from_deviation` 用沪深300相对 MA10 偏离映射到 5/10/20 只。
5. `small_cap_stop` 取固定止损、盈利保护和 ATR 止损中最高的有效止损线。组合引擎在下一可交易日开盘执行，并检查 T+1、停牌和涨跌停。

所有阈值来自 `config/default.yaml` 的 `small_cap` 节，Skill 不复制第二套公式。缺失字段自然落入不合格路径。

## 优化参数与代码路径

`select_small_cap` 还支持可选的 `price_min`、`amount20_min`、`momentum_20_min`、`momentum_60_min`、`ma60_ratio_min`、`volatility_max` 和 `exclude_growth_boards`。这些条件只读取主回测在信号日前预计算的历史窗口字段，不读取未来价格。`ranking_weights` 可以对 `size`、`roe`、`roa`、`momentum_20`、`momentum_60`、`low_volatility` 做加权百分位排序；默认仍为市值优先，因而不改变原始基线。


实际策略公式维护在本包 `src/qs_small_cap_fundamental/strategy.py`，主 Agent 的 `strategies.py` 仅重新导出接口。安装仓库后可直接 `import qs_small_cap_fundamental`；共享点时工具是基础依赖，不重复实现。

## 函数与公式对应

| 函数／代码段 | 计算与解释 |
|---|---|
| point_in_time_latest | 每股保留信号日前最新公开财务数据，避免未来财报进入筛选 |
| market_cap / roa | close × total_shares；net_profit ÷ total_assets，分母为零视为缺失 |
| fundamental_ok | 市值区间、营收下限、ROE/ROA/净利润正值同时成立 |
| audit_ok / dividend_ok | 最近公开年度审计必须通过；现金分红公告默认要求三年窗口内存在，可用 `require_recent_dividend: false` 作为预先声明的放宽候选，审计条件不可关闭 |
| eligible | 基本面、审计、分红、可交易性四个布尔值的交集 |
| holding_count_from_deviation | deviation = 最新收盘价 / 最近10日均价 − 1，严格大于/小于阈值才改变数量 |
| average_true_range | TR=max(high-low, abs(high-prev_close), abs(low-prev_close))；最近14日 TR 的算术均值 |
| small_cap_stop | 固定线=0.91×成本；保护线=1.01×成本（peak_close≥1.08×成本）；ATR线=peak_close−3×ATR |

止损取已激活三条线中的最大值：最高线最先被收盘价跌破。ATR 越大，ATR 线越低，但固定和成本保护线仍有效；ATR 放宽不覆盖固定止损。止损是收盘触发、下一可交易开盘执行，跳空或跌停会令实际损失大于 9%。

排序使用市值升序再按证券代码升序，保证同值情况下可复现。返回 target_count 与 selected_count 可以不同，合格证券不足时不补入不合格证券。

## 调用示例

```python
from qs_small_cap_fundamental import select_small_cap, small_cap_stop
selection = select_small_cap(signal_date, snapshot, financials, audits, dividends, index_history, cfg)
hit, stop, reason = small_cap_stop(cost, peak_close, close, atr14, cfg)
```

## 当前边界

当前最近年度审计按已公开年度审计记录选取，尚不强制与最新年度财报的 report_period 逐一匹配，正式验证需要补齐并核对对应关系。无法满足审计证据的时期不能据此声称严格策略已完整复现。
