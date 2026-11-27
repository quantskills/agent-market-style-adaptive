---
name: agent-market-style-adaptive
description: Coordinate point-in-time A-share small-cap selection, domestic-listed global ETF rotation, low-volatility blue-chip selection, market-regime allocation, portfolio risk controls, reproducible backtests, and attribution. Use for research workflows that need deterministic style adaptation from Pandadata evidence; never use it to place live orders.
quantSkills:
  schema_version: 2.0.0
  organization: quantskills
  organization_url: https://github.com/quantskills
  repository: agent-market-style-adaptive
  repository_url: https://github.com/quantskills/agent-market-style-adaptive
  project_type: agent
  license: GPL-3.0-only
  maintainer: community
  collection: portfolio-orchestration
  catalog: {category: "10", subcategory: 10.market-style-adaptive}
  workflow:
    primary_stage: orchestration
    workflow_stages:
      - data-ingestion
      - data-quality
      - feature-engineering
      - factor-screening
      - portfolio-construction
      - backtesting
      - evaluation
      - risk
      - reporting
      - orchestration
  tags: [a-share, style-rotation, small-cap, etf-rotation, low-volatility, regime, pandadata, backtest]
  requires:
    - skill-market-regime-analysis
    - skill-small-cap-fundamental
    - skill-global-etf-rotation
    - skill-low-vol-blue-chip
  summary_zh: 基于市场状态统一调度小市值、全球ETF和低波白马策略的可复现研究智能体。
  summary_en: Reproducible research agent that allocates among small-cap, global ETF, and low-volatility blue-chip sleeves by market regime.
  status: active
  validation_level: runnable
  maintainer_type: community
  platforms: [cursor, claude-code, codex, hermes, openclaw]
  interface: {mode: cli, command: market-style-agent}
---

# Market Style Adaptive Agent

Coordinate four skills and keep all trading calculations deterministic. Use the language model only to plan Pandadata MCP calls, inspect evidence, and explain results.

## Workflow

1. Read `references/data-contract.md` and run `market-style-agent data-sync` to generate or validate the MCP request and artifact manifest.
2. Use the Pandadata MCP methods named in that manifest. Materialize returned Parquet artifacts into the configured cache; never substitute another data source silently.
3. Run `market-style-agent backtest --mode adaptive`; use `--mode independent` only for sleeve diagnostics.
4. Run `market-style-agent compare`, then `market-style-agent report`.
5. Treat missing point-in-time evidence as ineligible. Do not fill financial data, backfill an ETF before listing, or use a present-day universe for historical dates.

## Skill routing

- Market classification: `skills/skill-market-regime-analysis/SKILL.md`, using its threshold method for the first release.
- Small-cap sleeve: `skills/skill-small-cap-fundamental/SKILL.md`.
- ETF sleeve: `skills/skill-global-etf-rotation/SKILL.md`.
- Blue-chip sleeve: `skills/skill-low-vol-blue-chip/SKILL.md`.

The Python package under `src/` is the canonical implementation. Skill documents describe when and how to invoke it; they must not fork the formulas.

## Boundaries

- Research and education only. Do not connect to a broker or place orders.
- Report observed results. The example return and drawdown in the project brief are not acceptance targets.
- Save the configuration hash, data manifest, data-quality findings, trades, blocked orders, and evidence for every run.
- Read `references/research-boundary.md` before publishing or presenting results.

