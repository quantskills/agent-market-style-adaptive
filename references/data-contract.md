# Data contract

All cached tables are Parquet files in `data/cache`. Dates are normalized to midnight and symbols use the `000001.SZ` form.

| File | Required columns |
| --- | --- |
| `stock_daily.parquet` | date, symbol, name, raw open/high/low/close/pre_close, adj_open/adj_high/adj_low/adj_close/adj_pre_close, amount, limit_up, limit_down, trade_status |
| `fund_daily.parquet` | date, symbol, raw open/high/low/close/pre_close, adj_open/adj_high/adj_low/adj_close/adj_pre_close, amount, limit_up, limit_down |
| `index_daily.parquet` | date, symbol, open, high, low, close |
| `fundamentals.parquet` | symbol, announcement_date, report_period, revenue, roe, total_assets, total_shares, bvps, net_profit |
| `audit_opinion.parquet` | symbol, date, quarter, audit_type, opinion |
| `dividend.parquet` | symbol, announcement_date, effective_date, ex_date, div_type |
| `security_master.parquet` | symbol, name, list_date, delist_date, board |

Fundamental rows become eligible on the first trading day after `announcement_date`. The MCP bridge normalizes `get_fina_performance` fields as `info_date -> announcement_date`, `end_date -> report_period`, `operating_revenue -> revenue`, `net_profit_parent -> net_profit`, and `roe_diluted -> roe`; ROA is computed as net profit divided by total assets. Market capitalization and PB are recomputed on each signal date from raw close, point-in-time total shares, and BVPS. Adjusted stock fields come from `get_stock_daily_pre`; raw fields remain available for price-limit checks.

`data-sync` writes `outputs/data_sync/mcp_requests.json`. The first two calls are small- and large-result preflights. The host Agent executes these calls with Pandadata MCP and writes a raw manifest whose artifacts contain `request_id`, `local_path`, `total_rows`, and `sha256`. A large response may have a truncated conversational preview, but the downloaded Parquet must contain `total_rows` rows and match the hash. Run `data-sync --raw-manifest RAW.json` to merge chunks, attach adjusted OHLC, normalize financial fields, and create `data/cache/manifest.json`. A remote-only `server_path`, partial local artifact, missing request, or hash mismatch is a hard failure. Preflight artifacts are diagnostic and are not merged into final tables.
