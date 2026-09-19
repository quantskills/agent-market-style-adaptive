# 小市值基本面策略 Skill

本 Skill 是可独立运行的小市值选股与止损组件。它读取信号日股票快照、点时财务、年度审计、现金分红和沪深300历史，输出入选证券及逐项筛选证据。计算代码通过 `examples/run_small_cap.py` 直接调用本 Skill 包 src/qs_small_cap_fundamental/strategy.py 中的公式实现。

## 目录

```text
skill-small-cap-fundamental/
├── SKILL.md
├── README.md
├── README.en.md
├── LICENSE
├── agents/openai.yaml
├── examples/run_small_cap.py
├── src/qs_small_cap_fundamental/strategy.py
├── tests/test_qs_small_cap_fundamental.py
└── references/
    ├── data-interface.md
    └── implementation.md
```

## 独立运行

```powershell
python skills/skill-small-cap-fundamental/examples/run_small_cap.py `
  --as-of 2026-05-29 --cache-dir data/cache --config config/default.yaml `
  --output outputs/skill-small-cap
```

输出 `selection.json` 和 `evidence.csv`。脚本严格使用信号日前已公告数据。完整组合回测仍由 `market-style-agent backtest` 统一执行成交、T+1 和组合资金约束。


实际策略公式维护在本包 `src/qs_small_cap_fundamental/strategy.py`，主 Agent 的 `strategies.py` 仅重新导出接口。安装仓库后可直接 `import qs_small_cap_fundamental`；共享点时工具是基础依赖，不重复实现。
