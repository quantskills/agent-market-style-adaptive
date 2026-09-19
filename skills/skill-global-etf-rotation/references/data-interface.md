# 数据接口与点时约束

所有日期按交易日日期处理。信号日 T 的行情可见，财务与审计公告日期必须严格早于 T；这是公告后下一交易日开始可用的保守规则。最终成交日期必须晚于信号日期。缓存由主 Agent 数据层校验后使用；Skill 不自行改换数据源。

价格单位为元，财务金额为元，total_shares 为股；市值区间 10–100 亿元对应 1e9–1e10 元。收益、波动率和 MA 偏离使用小数比例；ROE 仅用于正负及横截面排序，数据源单位保持一致。

股票快照需要 date、symbol、close、trade_status；股票所属板块从证券代码识别，ST 从当时股票名称识别。缺失 name 或 trade_status 的输入应由缓存校验拒绝，不能被解释为正常可交易。复权价格用于收益和波动指标，未复权 close 用于价格上限、PB 和市值。

输出 Selection.symbols 是有序代码列表，Selection.evidence 是逐证券 DataFrame。独立脚本输出 selection.json（as_of、selected、selected_count）与 evidence.csv。输出是候选和证据，不是成交记录。

## select_etfs 输入

| 参数 | 必要字段／含义 |
|---|---|
| as_of | 信号交易日 |
| fund_history | date、symbol、close、amount；存在 adj_close 时使用它计算收益与均线 |
| cfg | config/default.yaml 的 etf 节，含七只 ETF 代码、窗口和阈值 |

只读取截至信号日的真实 ETF 行情，必须至少有 121 个真实交易日才能计算 120 日收益；上市前不得加入指数历史。amount 为成交额，5/20 日均值应使用相同单位。

证据包含 history_days、ret3、score、ma20、ma60、amount5、amount20、five filters 和 eligible。历史不足时 eligible=False，指标为空。
