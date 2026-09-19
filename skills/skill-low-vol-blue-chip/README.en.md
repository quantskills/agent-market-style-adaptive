# Low-volatility Blue-chip Skill

A runnable monthly A-share blue-chip sleeve. The example computes trailing 60-day volatility from adjusted prices, applies point-in-time fundamentals, and calls the canonical `select_blue_chips` implementation.


实际策略公式维护在本包 `src/qs_low_vol_blue_chip/strategy.py`，主 Agent 的 `strategies.py` 仅重新导出接口。安装仓库后可直接 `import qs_low_vol_blue_chip`；共享点时工具是基础依赖，不重复实现。
