# Small-cap Fundamental Skill

A runnable sleeve package for point-in-time A-share small-cap selection and stop evaluation. `examples/run_small_cap.py` reads the canonical cache and calls the single implementation in `agent_market_style_adaptive.strategies`, producing selected symbols and auditable evidence. See `references/implementation.md` for the code-to-rule map.


实际策略公式维护在本包 `src/qs_small_cap_fundamental/strategy.py`，主 Agent 的 `strategies.py` 仅重新导出接口。安装仓库后可直接 `import qs_small_cap_fundamental`；共享点时工具是基础依赖，不重复实现。
