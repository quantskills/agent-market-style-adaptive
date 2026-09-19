# 数据接口与点时约束

所有日期按交易日日期处理。信号日 T 的行情可见，财务与审计公告日期必须严格早于 T；这是公告后下一交易日开始可用的保守规则。最终成交日期必须晚于信号日期。缓存由主 Agent 数据层校验后使用；Skill 不自行改换数据源。

价格单位为元，财务金额为元，total_shares 为股；市值区间 10–100 亿元对应 1e9–1e10 元。收益、波动率和 MA 偏离使用小数比例；ROE 仅用于正负及横截面排序，数据源单位保持一致。

股票快照需要 date、symbol、close、trade_status；股票所属板块从证券代码识别，ST 从当时股票名称识别。缺失 name 或 trade_status 的输入应由缓存校验拒绝，不能被解释为正常可交易。复权价格用于收益和波动指标，未复权 close 用于价格上限、PB 和市值。

输出 Selection.symbols 是有序代码列表，Selection.evidence 是逐证券 DataFrame。独立脚本输出 selection.json（as_of、selected、selected_count）与 evidence.csv。输出是候选和证据，不是成交记录。

## select_small_cap 输入

| 参数 | 必要字段／含义 |
|---|---|
| as_of | 信号交易日 |
| stock_snapshot | 当日 date、symbol、close、name、trade_status |
| fundamentals | symbol、announcement_date、report_period、revenue、roe、net_profit、total_assets、total_shares、bvps |
| audit | symbol、date（公告日）、audit_type、opinion；quarter 用于限定年度 Q4 |
| dividends | symbol、announcement_date、div_type |
| index_history | 沪深300截至当日的 date、close |
| cfg | config/default.yaml 的 small_cap 节 |

审计必须是已公布的年度财务审计标准无保留意见。annual_report_available_proxy 只表示年报存在，不是审计意见；代码不接受该代理。当前审计缓存真实意见始于 2023 年，此前不能完整验证小市值筛选。

## 止损接口

small_cap_stop(entry_price, peak_close, close, atr, cfg) 返回 (triggered, stop_price, reason)。四个价格参数必须使用同一复权基准。average_true_range(history, window=14) 接收 date、high、low、close；调用者应提前将这些字段转换到一致复权口径。
