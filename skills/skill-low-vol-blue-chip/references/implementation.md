# 代码实现说明

核心入口是 `select_blue_chips`，位于 `src/qs_low_vol_blue_chip/strategy.py`（本 Skill 包内）。独立脚本先用截至信号日的复权收盘价预计算每只股票 60 日年化波动率，再传入当日快照。

筛选顺序为：

1. 排除创业板、科创板、北交所、ST、停牌和 50 元以上股票；
2. 要求 `0 < PB <= 3`、营收大于 10 亿元、净利润与 ROE 为正；
3. 保留 60 日波动率最低的 30%；
4. 对低 PB、高 ROE、低波动率的百分位等权求平均；
5. 根据市场情绪取 15、10 或 5 只并等权。

财务数据必须满足公告日早于信号日。所有参数来自 `config/default.yaml` 的 `blue_chip` 节。


实际策略公式维护在本包 `src/qs_low_vol_blue_chip/strategy.py`，主 Agent 的 `strategies.py` 仅重新导出接口。安装仓库后可直接 `import qs_low_vol_blue_chip`；共享点时工具是基础依赖，不重复实现。

## 优化参数与代码路径

`low_vol_quantile` 控制低波候选分位；`momentum_60_min` 和 `ma60_ratio_min` 是可选趋势门槛；`ranking_weights` 可对 `pb`、`roe`、`low_volatility`、`momentum_60`、`momentum_120` 加权。参数搜索只使用 2024-12-31 以前的折段，冻结样本外结果不会回写配置。

`allowed_regimes` 是可选的市场状态白名单。状态由截至信号日的沪深300趋势和波动率确认后传入；不在白名单时返回空候选，资金由组合层保留为现金。未配置时不启用状态门控。

## 逐段代码解释

select_blue_chips 先截断历史、每股取最新行情，再合并公告日期严格早于信号日的基本面。PB=未复权 close/bvps，分母零值视为缺失。证券代码前缀排除成长板块和北交所，名称识别 ST；base_ok 再叠加价格、估值、规模、盈利及可交易条件。

若未传入 volatility，函数计算各证券复权收盘价的日收益，用最近60个价格对应的59个有效收益样本估计标准差，再乘 sqrt(252)。历史不足者波动率为空并退出候选。这个样本口径与主引擎预计算保持一致。

在 base_ok 候选中计算波动率 30% 分位阈值，保留 volatility≤阈值，因此分位处并列值可能使保留数量超过30%。排序分数为三个百分位的均值：

```python
score = (pb.rank(pct=True, ascending=False)
       + roe.rank(pct=True, ascending=True)
       + volatility.rank(pct=True, ascending=False)) / 3
```

PB 越低、ROE 越高、波动越低，百分位分数越高。按 score 降序、symbol 升序排序，并按 cold/warm/overheated 的15/10/5只截取。crisis 映射 cold。组合引擎对实际候选等权，合格不足不补入高风险证券。

## 调用示例

```python
from qs_low_vol_blue_chip import select_blue_chips
selection = select_blue_chips(signal_date, stock_history, financials, "warm", cfg)
```

## 当前边界

脚本和主引擎使用当日行情快照，只选当天有行情的证券。传入自行预计算的波动率时，调用者需提供截至信号日的计算证据。
