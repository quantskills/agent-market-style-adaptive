# 低波白马策略 Skill

本 Skill 是可独立运行的月频低波白马选股组件。它根据点时基本面、板块与 ST 排除、价格和交易状态过滤，再从最低波动率 30% 中综合 PB、ROE 与波动率排序。

## 目录

```text
skill-low-vol-blue-chip/
├── SKILL.md
├── README.md
├── README.en.md
├── LICENSE
├── agents/openai.yaml
├── examples/run_low_vol_blue_chip.py
├── src/qs_low_vol_blue_chip/strategy.py
├── tests/test_qs_low_vol_blue_chip.py
└── references/
    ├── data-interface.md
    └── implementation.md
```

## 独立运行

```powershell
python skills/skill-low-vol-blue-chip/examples/run_low_vol_blue_chip.py `
  --as-of 2026-05-29 --sentiment warm --cache-dir data/cache `
  --config config/default.yaml --output outputs/skill-blue-chip
```

输出 `selection.json` 和 `evidence.csv`。`cold/warm/overheated` 分别持有 15/10/5 只。


实际策略公式维护在本包 `src/qs_low_vol_blue_chip/strategy.py`，主 Agent 的 `strategies.py` 仅重新导出接口。安装仓库后可直接 `import qs_low_vol_blue_chip`；共享点时工具是基础依赖，不重复实现。
