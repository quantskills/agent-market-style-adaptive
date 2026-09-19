# 市场风格自适应策略切换 Agent

[English](README.en.md)

[![QuantSkills schema](https://img.shields.io/badge/QuantSkills-2.0.0-2f6f9f)](AGENTS.md)
[![Status](https://img.shields.io/badge/status-runnable-2f6f9f)](AGENTS.md)
[![License](https://img.shields.io/badge/license-GPL--3.0--only-1f2937)](LICENSE)

> 基于 PandaData 点时证据，统一调度市场状态、小市值、境内上市 ETF 和低波白马策略的可复现研究 Agent。

本项目属于 QuantSkills 社区的 `agent-*` 类型仓库。它负责生成数据请求、执行确定性回测、记录交易与归因并输出报告；不连接券商，不执行实盘订单，也不承诺收益。原始策略材料中的收益和回撤数字仅作为待验证的研究假设，不是验收目标。

## 核心能力

| 模块 | 作用 |
| --- | --- |
| 市场状态 Agent | 根据指数趋势、波动和偏离度识别冷/暖/过热状态，并提供组合调度信号 |
| 小市值 Agent | 点时市值、营收、ROE、ROA、净利润、审计与分红约束；支持固定、利润保护和 ATR 止损 |
| ETF Agent | 在黄金、纳指、创业板、上证、恒生、科创板、国债等境内上市 ETF 中执行跌幅、动量、均线和成交量过滤 |
| 白马 Agent | 低波、质量、PB、趋势和板块排除条件组合筛选白马股票 |
| 主编排 Agent | 固定 40%/40%/20% 大类预算，在子策略内部轮动，并记录风险缩放、持仓变化和策略贡献 |

ETF 参数在当前 JoinQuant 复刻配置中冻结；小市值和白马候选可独立研究优化。所有指标、仓位、成交、成本和状态切换均由 Python 确定性计算。

## Agent 协作流程

1. `data-sync` 生成 PandaData MCP 请求计划和数据契约。
2. 宿主 MCP bridge 执行请求，返回完整 Parquet 和 SHA-256 manifest。
3. Agent 校验行数、哈希、上市日期、公告日期和点时可用性，拒绝未来函数与缺失证据。
4. 运行自适应组合回测，并可运行三个子策略独立诊断。
5. `compare` 计算组合及各子策略相对沪深 300 的年化超额和累计超额。
6. `report` 输出最新持仓、每次调仓变化、止损/过滤/未成交记录、市场状态和策略贡献。

模型只负责组织 MCP 调用、检查证据和解释结果；它不替代回测引擎的公式。四个策略文档位于 [`skills/`](skills/)，统一受 [`AGENTS.md`](AGENTS.md) 的 QuantSkills 元数据约束。

## 项目结构

```text
AGENTS.md                         QuantSkills 2.0 Agent 声明
skills/                           四个可复用策略 Skill
src/agent_market_style_adaptive/  Python 主实现、数据同步、回测和报告
agents/                           OpenAI 与便携式加载入口
config/                           默认、复刻和研究配置
references/                       数据契约与研究边界
scripts/                          数据抓取、评估、报告和发布校验
tests/                            单元、回测可重复性和数据质量测试
.github/workflows/validate.yml    社区包校验工作流
```

## 安装

```powershell
py -3.11 -m venv .venv
.venv\\Scripts\\python -m pip install -e ".[test]"
```

命令行入口为 `market-style-agent`。需要 Python 3.11 或更高版本。

## PandaData 数据配置

PandaData 是本 Agent 的唯一数据源。仓库中的 Parquet 仅是运行时物化和审计产物，不是可替代的数据源；发布包不包含本地缓存、历史报告或用户凭据。使用者必须自行提供 PandaData MCP bridge，并通过环境变量或宿主配置暴露命令：

```powershell
$env:PANDADATA_MCP_COMMAND = '<your-pandadata-mcp-bridge>'
```

凭据只能由宿主在运行时注入，不能写入 README、配置、日志、报告或 Git。数据字段、点时规则、分块返回和哈希要求见 [`references/data-contract.md`](references/data-contract.md)。

## 快速开始

以下配置用于 2018-01-02 至缓存最新交易日的 PandaData 复刻；正式终点必须以 manifest 中的最后交易日为准：

```powershell
market-style-agent --config config/pandadata_exact_20260918_adaptive_only.yaml data-sync --execute
market-style-agent --config config/pandadata_exact_20260918_adaptive_only.yaml backtest --mode adaptive
market-style-agent --config config/pandadata_exact_20260918_adaptive_only.yaml backtest --mode independent
market-style-agent --config config/pandadata_exact_20260918_adaptive_only.yaml compare
market-style-agent --config config/pandadata_exact_20260918_adaptive_only.yaml report
```

若仅需检查同步结果，可使用 `data-sync --validate-only`。没有有效的 PandaData 同步回执时，回测命令会拒绝使用旧缓存。

## 输出与审计

每次运行在配置指定的 `outputs/<run>/` 下保存：

- `report.html`：组合、三个独立子策略、沪深 300 对比、累计收益图和最新持仓
- 净值、回撤、成交、交易成本和换手明细
- `holdings_history.csv`：每次调仓的新增、减少、保留和清仓证券
- 市场状态、风险预算、子策略贡献和止损/过滤/涨跌停未成交记录
- 配置快照、配置 hash、PandaData manifest、同步回执和数据质量发现

报告采用价格指数口径比较沪深 300；若没有完整审计证据，候选会被剔除，不能用代理字段补造审计意见。

## 回测口径与限制

- 收盘生成信号，下一可交易日开盘成交；遵守 T+1、停牌、涨跌停和交易成本约束。
- 股票信号使用前复权行情，涨跌停使用原始行情；财务数据从公告后的下一交易日开始可用。
- 小市值硬条件包括 10--100 亿元市值、营收大于 1 亿元、ROE/ROA 大于 0、净利润大于 200 万，并叠加审计与分红约束。
- 沪深 300 使用 `000300.SH` 价格指数，不将自然日当作交易日。
- 这是日线近似复刻，不声称盘中或 JoinQuant 内部撮合的一比一复制。
- 研究边界、已知偏差和风险说明见 [`references/research-boundary.md`](references/research-boundary.md)。

## 开发与发布

```powershell
python scripts/validate_package.py
pytest -q
python -m compileall -q src skills
```

发布前必须通过上述检查，并确认压缩包不含 `data/`、`outputs/`、`.pytest_cache/`、`__pycache__/`、本地构建目录或任何凭据。社区字段、目录命名和许可证要求见 [`COMMUNITY_RULES.md`](COMMUNITY_RULES.md)。当前状态为 `runnable`，未经完整数据和无未来函数审计不得宣称 `verified`、官方认证或 QuantSkills 背书。

## License

本项目采用 [GPL-3.0-only](LICENSE)。
