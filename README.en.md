# Market Style Adaptive Strategy-Switching Agent

[简体中文](README.md)

[![QuantSkills schema](https://img.shields.io/badge/QuantSkills-2.0.0-2f6f9f)](AGENTS.md)
[![Status](https://img.shields.io/badge/status-runnable-2f6f9f)](AGENTS.md)
[![License](https://img.shields.io/badge/license-GPL--3.0--only-1f2937)](LICENSE)

> A reproducible research agent that coordinates regime detection, small-cap selection, domestic-listed ETF rotation, and low-volatility blue chips from point-in-time PandaData evidence.

This is a QuantSkills `agent-*` repository. It plans data requests, runs deterministic backtests, records trades and attribution, and publishes auditable reports. It has no broker integration, does not place live orders, and makes no return guarantees. Historical figures from source strategy material are research hypotheses only.

## Capabilities

| Module | Role |
| --- | --- |
| Market-regime agent | Classifies cold, warm, and overheated states from index trend, volatility, and moving-average deviation |
| Small-cap agent | Point-in-time market cap, revenue, ROE, ROA, profit, audit, dividend, and triple-stop rules |
| ETF agent | Five-stage downside, momentum, moving-average, and volume filtering across seven domestic-listed ETFs |
| Blue-chip agent | Low-volatility, quality, PB, trend, board-exclusion, and price filters |
| Orchestrator | Keeps the strategic 40%/40%/20% sleeve budget while rotating within sleeves and recording risk and attribution |

ETF parameters are frozen in the current JoinQuant-replica configuration; small-cap and blue-chip candidates may be researched independently. Calculations are deterministic Python code.

## Agent workflow

1. `data-sync` creates the PandaData MCP request plan.
2. The host MCP bridge materializes complete Parquet artifacts and a SHA-256 manifest.
3. The agent validates row counts, hashes, listing dates, announcement dates, and point-in-time eligibility.
4. Adaptive and independent sleeve backtests are run.
5. `compare` calculates annualized and cumulative excess return versus CSI 300.
6. `report` writes holdings, rebalance changes, stops, blocked orders, regimes, and attribution.

The model organizes MCP calls and explains evidence; the Python engine owns formulas and execution assumptions. See [`skills/`](skills/) and [`AGENTS.md`](AGENTS.md).

## Repository layout

```text
AGENTS.md                         QuantSkills 2.0 declaration
skills/                           Four reusable strategy skills
src/agent_market_style_adaptive/  Data sync, backtest, attribution, reporting
agents/                           OpenAI and portable loader entrypoints
config/                           Default and replication configurations
references/                       Data contract and research boundary
scripts/                          Fetch, evaluation, reporting, validation
tests/                            Unit and reproducibility tests
.github/workflows/validate.yml    Community package validation
```

## Install and run

```powershell
py -3.11 -m venv .venv
.venv\\Scripts\\python -m pip install -e ".[test]"
$env:PANDADATA_MCP_COMMAND = '<your-pandadata-mcp-bridge>'
market-style-agent --config config/pandadata_exact_20260918_adaptive_only.yaml data-sync --execute
market-style-agent --config config/pandadata_exact_20260918_adaptive_only.yaml backtest --mode adaptive
market-style-agent --config config/pandadata_exact_20260918_adaptive_only.yaml backtest --mode independent
market-style-agent --config config/pandadata_exact_20260918_adaptive_only.yaml compare
market-style-agent --config config/pandadata_exact_20260918_adaptive_only.yaml report
```

PandaData is the only data source. Local Parquet files are runtime materializations and audit artifacts, not a substitute data source. Credentials must be supplied by the host at runtime and must never be committed. Without a valid sync receipt, backtests reject stale cache use. See [`references/data-contract.md`](references/data-contract.md).

## Outputs and limitations

Each run writes `outputs/<run>/report.html`, independent sleeve metrics, cumulative-return charts, CSI 300 excess metrics, trades, costs, turnover, `holdings_history.csv`, regime and contribution records, blocked orders, configuration hash, manifest, sync receipt, and data-quality findings. The report uses the CSI 300 price-index convention and the latest manifest trading date. Signals are generated at close and filled at the next tradable open under T+1, suspension, limit, and cost assumptions. This is a daily-bar approximation, not an intraday one-to-one reproduction. See [`references/research-boundary.md`](references/research-boundary.md).

## Validate and publish

```powershell
python scripts/validate_package.py
pytest -q
python -m compileall -q src skills
```

Release archives must exclude `data/`, `outputs/`, caches, build artifacts, and credentials. The repository is currently `runnable`, not `verified` or officially endorsed by QuantSkills.

## License

[GPL-3.0-only](LICENSE)
