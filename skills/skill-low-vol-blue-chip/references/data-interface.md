# 数据接口与点时约束

所有日期按交易日日期处理。信号日 T 的行情可见，财务与审计公告日期必须严格早于 T；这是公告后下一交易日开始可用的保守规则。最终成交日期必须晚于信号日期。缓存由主 Agent 数据层校验后使用；Skill 不自行改换数据源。

价格单位为元，财务金额为元，total_shares 为股；市值区间 10–100 亿元对应 1e9–1e10 元。收益、波动率和 MA 偏离使用小数比例；ROE 仅用于正负及横截面排序，数据源单位保持一致。

股票快照需要 date、symbol、close、trade_status；股票所属板块从证券代码识别，ST 从当时股票名称识别。缺失 name 或 trade_status 的输入应由缓存校验拒绝，不能被解释为正常可交易。复权价格用于收益和波动指标，未复权 close 用于价格上限、PB 和市值。

输出 Selection.symbols 是有序代码列表，Selection.evidence 是逐证券 DataFrame。独立脚本输出 selection.json（as_of、selected、selected_count）与 evidence.csv。输出是候选和证据，不是成交记录。

## select_blue_chips 输入

| 参数 | 必要字段／含义 |
|---|---|
| as_of | 信号交易日 |
| stock_history | date、symbol、close、name、trade_status；adj_close 可用于复权收益 |
| fundamentals | symbol、announcement_date、bvps、revenue、net_profit、roe |
| sentiment | cold、warm、overheated；crisis 使用 cold 持股数量 |
| cfg | config/default.yaml 的 blue_chip 节 |

stock_history 可提供预计算 volatility 的当日快照，此时函数直接使用该字段；否则用历史价格计算 60 日年化波动率。预计算字段必须只依赖截至当日的历史数据，不能传入全样本波动率。

证据包含 PB、base_ok、volatility、score、eligible、target_count。eligible 表示进入低波候选池，不等于最终持仓，最终代码以 symbols 为准。
