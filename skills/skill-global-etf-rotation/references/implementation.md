# 代码实现说明

核心入口是 `select_etfs`，位于 `src/qs_global_etf_rotation/strategy.py`（本 Skill 包内）。

对池内每只 ETF，代码先要求至少 121 个真实交易日，再计算：

- `ret3 >= -5%`；
- `score = 0.5*ret20 + 0.3*ret60 + 0.2*ret120 > 0`；
- 收盘价高于 MA20 且 MA20 高于 MA60；
- 5 日平均成交额高于 20 日平均成交额。

通过全部过滤后按得分降序、代码升序确定性排序，取前两只。组合层把 ETF 袖套权重平均分为两份；只有一只合格时另一份保留现金。


实际策略公式维护在本包 `src/qs_global_etf_rotation/strategy.py`，主 Agent 的 `strategies.py` 仅重新导出接口。安装仓库后可直接 `import qs_global_etf_rotation`；共享点时工具是基础依赖，不重复实现。

## 优化参数与代码路径

`minimum_score` 调整加权动量最低阈值；`require_all_momentum_positive` 控制 20/60/120 日收益是否必须全部为正；`require_amount_confirmation`、`require_above_short_ma` 和 `require_ma_order` 分别控制成交额、价格站上短均线和均线多头确认；`max_positions` 和 `momentum_weights` 控制持仓广度与动量期限。三个确认开关默认均为 `true`，因此旧配置行为不变。所有窗口都在 `date <= as_of` 的真实 ETF 行情上计算，主引擎随后按下一交易日成交。

`relative_strength_min_percentile` 可要求 ETF 的同日加权动量处于池内指定百分位以上。`fixed_stop`、`trailing_stop` 与 `trailing_trigger` 是组合执行层的可选退出规则；信号在收盘后生成，下一可交易日开盘执行。未配置时完全保持旧行为。

## 逐段代码解释

select_etfs 先截断 date≤as_of 并限定配置标的池，然后逐 ETF 排序、按日期去重。minimum=max(momentum_windows)+1，用于确保收益公式的两端价格都真实存在。

retN=close[T]/close[T−N]−1。20、60、120 日收益按 0.5、0.3、0.2 加权得到 score；代码要求加权 score>0，并不要求每个单独窗口都为正。

五个条件对应 three_day_ok（ret3≥−0.05）、momentum_ok（score>0）、above_ma_ok（close>MA20）、ma_order_ok（MA20>MA60）、amount_ok（amount5>amount20）。all(checks.values()) 生成 eligible。所有均线均包含信号日收盘数据。

最终按 score 降序、symbol 升序取 max_positions=2；不足两只只返回实际候选。主 Agent 将 ETF 预算分两份，因此一只合格仅使用一半 ETF 预算，另一半留现金。Skill 本身不擅自将一只候选加到整个预算。

## 调用示例

```python
from qs_global_etf_rotation import select_etfs
selection = select_etfs(signal_date, fund_history, cfg)
print(selection.symbols)
print(selection.evidence[["symbol", "score", "eligible"]])
```

## 当前边界

输入行情须来自经过上市日期校验的缓存。函数检查真实历史长度，但不会另外读取 security_master 判断上市日期；数据层承担此约束。
