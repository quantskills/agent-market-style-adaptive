# Global ETF Rotation Skill

A runnable seven-fund trend-rotation package. Its example entry point calls the canonical `select_etfs` implementation and writes selected funds plus all filter evidence. It never synthesizes pre-listing history.


实际策略公式维护在本包 `src/qs_global_etf_rotation/strategy.py`，主 Agent 的 `strategies.py` 仅重新导出接口。安装仓库后可直接 `import qs_global_etf_rotation`；共享点时工具是基础依赖，不重复实现。
