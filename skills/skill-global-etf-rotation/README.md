# 全球 ETF 轮动策略 Skill

本 Skill 是可独立运行的七只境内上市 ETF 趋势轮动组件。它按周计算三日跌幅、20/60/120 日动量、MA20/MA60 和成交额确认，输出最多两只 ETF 与完整证据。

## 目录

```text
skill-global-etf-rotation/
├── SKILL.md
├── README.md
├── README.en.md
├── LICENSE
├── agents/openai.yaml
├── examples/run_etf_rotation.py
├── src/qs_global_etf_rotation/strategy.py
├── tests/test_qs_global_etf_rotation.py
└── references/
    ├── data-interface.md
    └── implementation.md
```

## 独立运行

```powershell
python skills/skill-global-etf-rotation/examples/run_etf_rotation.py `
  --as-of 2026-05-29 --cache-dir data/cache --config config/default.yaml `
  --output outputs/skill-etf-rotation
```

脚本输出 `selection.json` 和 `evidence.csv`。上市前历史不会由指数或其他基金替代；不足两只时只返回合格标的。


实际策略公式维护在本包 `src/qs_global_etf_rotation/strategy.py`，主 Agent 的 `strategies.py` 仅重新导出接口。安装仓库后可直接 `import qs_global_etf_rotation`；共享点时工具是基础依赖，不重复实现。
