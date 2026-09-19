---
name: small-cap-fundamental
description: Build or audit the point-in-time A-share small-cap sleeve used by agent-market-style-adaptive, including financial, audit, dividend, dynamic breadth, and three-stop controls. Use for this sleeve's selection evidence or deterministic backtest; not for generic stock recommendations.
license: GPL-3.0-only
metadata:
  quantSkills:
    organization: quantskills
    repository: agent-market-style-adaptive
    project_type: skill
    collection: portfolio-orchestration
    status: active
    validation_level: runnable
---

# 小市值基本面选股

本 Skill 提供可直接调用的确定性 Python 策略，与市场状态分析 Skill 一样按数据采集、指标计算、筛选和结果交接组织。用于独立研究或由主 Agent 调度。

## Data Sources (PandaData)

读取已通过主 Agent 数据契约校验的 PandaData Parquet 缓存。字段、点时规则和输入示例见 [数据接口](references/data-interface.md)。需要新增数据时遵循根目录 references/data-contract.md 的 MCP 请求与落地流程。

## Strategy Implementation

实际公式位于 [strategy.py](src/qs_small_cap_fundamental/strategy.py)，入口为 `select_small_cap`。返回 `Selection(symbols, evidence)`：symbols 是排序后的候选，evidence 是筛选与指标证据。公式逐项说明见 [实现说明](references/implementation.md)。主 Agent 的 strategies.py 仅重新导出这些接口，不维护另一套公式。

## Workflow

1. 验证缓存字段与公告日期，读取截至信号日的数据。
2. 读取 config/default.yaml 的 `small_cap` 节，调用本包策略函数。
3. 导出候选与证据。候选产生后由组合引擎处理资金分配、下一交易日成交、T+1、停牌、涨跌停和交易费用。
4. 复核缺失证据及未成交原因；缺失基本面不插补，ETF 上市前不补造历史。

## Executable Example

在仓库根目录安装 `python -m pip install -e .` 后运行：

```powershell
python skills/skill-small-cap-fundamental/examples/run_small_cap.py --as-of 2026-05-29 --cache-dir data/cache --config config/default.yaml --output outputs/examples/small-cap-fundamental
```

输出 selection.json 和 evidence.csv。脚本包含完整数据读取、参数加载、策略调用和输出代码；它执行单日研究，不下单。

## References

| 文件 | 用途 |
|---|---|
| references/data-interface.md | 数据字段、单位与点时约束 |
| references/implementation.md | 函数职责、公式和代码解释 |
| examples/run_small_cap.py | 真实缓存的可执行示例 |
| src/qs_small_cap_fundamental/strategy.py | 本 Skill 的策略实现 |
| tests/test_qs_small_cap_fundamental.py | 本 Skill 行为回归测试 |

本包依赖仓库提供的 strategy_common 点时工具和 Selection 类型；当前随 Agent 统一安装，尚未作为独立发行包发布。

## Agent 联动契约

本 Skill 是小市值子策略 Agent，接收市场状态和风险预算后，在自身额度内完成点时选股。

**输入**：`RegimeSignal`、`SleeveBudget`、`as_of`、数据 manifest、上一期持仓和止损事件。

**输出**：`SleeveSignal`：

```yaml
sleeve: small_cap
as_of: YYYY-MM-DD
candidates: [symbol...]
target_count: N
orders: [{symbol: ..., side: buy|sell, reason: ...}]
evidence: evidence.csv
stop_events: [{symbol: ..., type: fixed|profit_protection|atr}]
```

候选必须满足小市值基本面、点时财务、审计/分红和可交易约束；不得改变大类预算。市场状态只影响持股数量、排序阈值和集中度，不改变数据口径。

**下游关系**：`SleeveSignal` 回传组合编排层，由其统一做风险预算、下一交易日成交、涨跌停/停牌处理和交易成本计算。止损卖出释放的资金只能在同一大类额度内再分配或留现金。

### 统一编排协议

- **评估节奏**：组合层按周或半月触发一次完整评估；同一评估日只生成一份 `as_of` 快照。止损事件可在两次评估之间触发被动卖出，但不得提前使用未来数据。
- **`SleeveBudget`**：由组合编排层下发，至少包含 `sleeve`、`target_weight`、`max_weight`、`cash_reserve`、`rebalance_id` 和 `as_of`。子策略只能在自身预算内调整持仓。
- **`StopEvent`**：统一记录 `symbol`、`type`、`trigger_price`、`trigger_date`、`reason` 和 `source`；止损事件优先处理，释放资金默认留在同一大类或转现金。
- **闭环顺序**：`RegimeSignal` → `SleeveBudget` → 三个 `SleeveSignal` 并行生成 → 组合层执行成交约束与成本 → 写入持仓快照、变更明细和归因 → 下一评估日复核。
- **证据要求**：每个输出必须携带数据 manifest、配置版本/hash、点时筛选证据和不可交易原因；缺失证据的标的不得进入候选或成交。
