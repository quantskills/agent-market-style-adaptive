from __future__ import annotations

from dataclasses import dataclass
import copy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import config_hash
from .data import load_tables
from .portfolio import RiskOverlay, target_allocation
from .regime import classify_regimes
from .strategies import (Selection, blue_chip_stop, etf_stop,
                         holding_count_from_deviation, select_blue_chips,
                         select_etfs, select_small_cap, small_cap_stop)


@dataclass
class Position:
    symbol: str
    sleeve: str
    shares: int
    average_cost: float
    acquired_date: pd.Timestamp
    peak_close: float
    # Keep the security label that was visible when the position was opened.
    # A current security-master name is useful for navigation, but must not be
    # used to describe a historical signal (e.g. a later ST/退名 change).
    name_asof_signal: str = ""
    name_source: str = ""


@dataclass
class BacktestResult:
    mode: str
    equity: pd.DataFrame
    trades: pd.DataFrame
    holdings: pd.DataFrame
    regimes: pd.DataFrame
    contributions: pd.DataFrame
    evidence: pd.DataFrame
    blocked_orders: pd.DataFrame
    allocations: pd.DataFrame
    metadata: dict[str, Any]

    def write(self, output_dir: str | Path) -> Path:
        root = Path(output_dir) / self.mode
        root.mkdir(parents=True, exist_ok=True)
        self.equity.to_csv(root / "equity.csv", index=False)
        self.trades.to_csv(root / "trades.csv", index=False)
        self.holdings.to_csv(root / "holdings.csv", index=False)
        self.regimes.to_csv(root / "regimes.csv", index=False)
        self.contributions.to_csv(root / "strategy_contributions.csv", index=False)
        self.evidence.to_csv(root / "selection_evidence.csv", index=False)
        self.blocked_orders.to_csv(root / "blocked_orders.csv", index=False)
        self.allocations.to_csv(root / "allocation_history.csv", index=False)
        pd.Series(self.metadata).to_json(root / "run_summary.json", force_ascii=False, indent=2)
        return root


class Backtester:
    def __init__(self, config: dict, tables: dict[str, pd.DataFrame]):
        self.config = config
        # The market cache can exceed ten million rows. Keep the loaded frames by
        # reference and add derived columns in place instead of doubling memory.
        self.tables = tables
        # These mappings are populated from the optional security master below.
        # Initialise them before _prepare(), which enriches the daily frame with
        # point-in-time listing and industry fields.
        self.symbol_names: dict[str, str] = {}
        self.symbol_list_dates: dict[str, Any] = {}
        self.symbol_delist_dates: dict[str, Any] = {}
        self.symbol_industries: dict[str, str] = {}
        self._prepare()
        self._selection_cache = {}
        # Optional point-in-time index constituent snapshots fetched directly
        # from PandaData.  The cache is intentionally optional so historical
        # runs made before the reference-universe refresh remain reproducible.
        self.index_weights: dict[str, pd.DataFrame] = {}
        weights_root = Path(config["data"]["cache_dir"]).parent / "index_weights_pandadata"
        if weights_root.is_dir():
            for symbol in ("000300.SH", "399100.SZ", "399101.SZ"):
                path = weights_root / f"{symbol.replace('.', '_')}.parquet"
                if path.is_file():
                    frame = pd.read_parquet(path)
                    if {"date", "stock_symbol"}.issubset(frame.columns):
                        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
                        self.index_weights[symbol] = frame.dropna(subset=["date"])
        self.float_reference = pd.DataFrame()
        reference_path = config.get("data", {}).get("reference_share_float")
        if reference_path:
            path = Path(reference_path)
            if not path.is_absolute():
                path = Path.cwd() / path
            if path.is_file():
                self.float_reference = pd.read_parquet(path)
                self.float_reference["date"] = pd.to_datetime(self.float_reference["date"], errors="coerce")
                # ``merge_asof`` requires the merge key to be globally sorted;
                # sorting by the grouping key first raises ``left keys must be
                # sorted`` on recent pandas versions even when ``by`` is set.
                self.float_reference = (
                    self.float_reference.dropna(subset=["date", "symbol"])
                    .drop_duplicates(["date", "symbol"], keep="last")
                    .sort_values(["date", "symbol"], kind="stable")
                    .reset_index(drop=True)
                )
        master = self.tables.get("security_master", pd.DataFrame())
        self.symbol_names = (
            master.drop_duplicates("symbol", keep="last").set_index("symbol")["name"].astype(str).to_dict()
            if {"symbol", "name"}.issubset(master.columns) else {}
        )
        self.symbol_list_dates = (
            master.drop_duplicates("symbol", keep="last").set_index("symbol")["list_date"].to_dict()
            if {"symbol", "list_date"}.issubset(master.columns) else {}
        )
        self.symbol_delist_dates = (
            master.drop_duplicates("symbol", keep="last").set_index("symbol")["delist_date"].to_dict()
            if {"symbol", "delist_date"}.issubset(master.columns) else {}
        )
        self.symbol_industries = {}
        industry_path = config.get("data", {}).get("reference_security")
        if industry_path:
            path = Path(industry_path)
            if not path.is_absolute():
                path = Path.cwd() / path
            if path.is_file():
                reference = pd.read_parquet(path)
                if {"symbol", "sector_code_name"}.issubset(reference.columns):
                    self.symbol_industries = reference.drop_duplicates("symbol").set_index("symbol")["sector_code_name"].fillna("unknown").astype(str).to_dict()

    @classmethod
    def from_cache(cls, config: dict) -> "Backtester":
        return cls(config, load_tables(config["data"]["cache_dir"]))

    def _prepare(self) -> None:
        for name in ("stock_daily", "fund_daily", "index_daily"):
            frame = self.tables[name]
            if not pd.api.types.is_datetime64_any_dtype(frame["date"]):
                frame["date"] = pd.to_datetime(frame["date"])
            if not frame["date"].is_monotonic_increasing:
                self.tables[name] = frame.sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
        stock = self.tables["stock_daily"]
        price = "adj_close" if "adj_close" in stock else "close"
        high = "adj_high" if "adj_high" in stock else "high"
        low = "adj_low" if "adj_low" in stock else "low"
        previous = stock.groupby("symbol", sort=False)[price].shift(1)
        true_range = pd.concat(
            [(stock[high] - stock[low]).abs(), (stock[high] - previous).abs(), (stock[low] - previous).abs()], axis=1
        ).max(axis=1)
        atr_window = int(self.config["small_cap"]["atr_window"])
        stock["atr14"] = (
            true_range.groupby(stock["symbol"], sort=False)
            .rolling(atr_window, min_periods=atr_window).mean().reset_index(level=0, drop=True)
        )
        grouped_price = stock.groupby("symbol", sort=False)[price]
        returns = grouped_price.pct_change(fill_method=None)
        for column in ("close", "limit_up", "limit_down", "trade_status", "name"):
            if column in stock.columns:
                stock[f"prev_{column}"] = stock.groupby("symbol", sort=False)[column].shift(1)
        stock["ret10"] = stock.groupby("symbol", sort=False)["close"].pct_change(9, fill_method=None)
        if self.symbol_list_dates:
            stock["list_date"] = stock["symbol"].map(self.symbol_list_dates)
        if self.symbol_delist_dates:
            stock["delist_date"] = stock["symbol"].map(self.symbol_delist_dates)
        if self.symbol_industries:
            stock["industry"] = stock["symbol"].map(self.symbol_industries)
        for window in (20, 60, 120):
            stock[f"momentum_{window}"] = grouped_price.pct_change(window, fill_method=None)
        stock["ma20_ratio"] = (
            stock[price] / grouped_price.rolling(20, min_periods=20).mean().reset_index(level=0, drop=True) - 1.0
        )
        stock["ma60_ratio"] = (
            stock[price] / grouped_price.rolling(60, min_periods=60).mean().reset_index(level=0, drop=True) - 1.0
        )
        stock["amount20"] = (
            stock["amount"].groupby(stock["symbol"], sort=False)
            .rolling(20, min_periods=20).mean().reset_index(level=0, drop=True)
        )
        vol_window = int(self.config["blue_chip"]["volatility_window"])
        stock["volatility"] = (
            returns.groupby(stock["symbol"], sort=False)
            .rolling(vol_window - 1, min_periods=vol_window - 1).std().reset_index(level=0, drop=True) * np.sqrt(252)
        )
        fund = self.tables["fund_daily"]
        fund_price = "adj_close" if "adj_close" in fund else "close"
        fund_returns = fund.groupby("symbol", sort=False)[fund_price].pct_change(fill_method=None)
        fund["ret3"] = fund.groupby("symbol", sort=False)[fund_price].pct_change(3, fill_method=None)
        fund["volatility"] = (
            fund_returns.groupby(fund["symbol"], sort=False)
            .rolling(vol_window - 1, min_periods=vol_window - 1).std().reset_index(level=0, drop=True) * np.sqrt(252)
        )
        benchmark = self.config["run"]["benchmark"]
        idx = self.tables["index_daily"]
        idx = idx[idx["symbol"] == benchmark].copy()
        self.regimes = classify_regimes(idx, self.config["regime"])
        self.regime_by_date = self.regimes.set_index("date")["regime"]
        # A date-only index supports fast daily slicing without a second full-size
        # symbol level. Only the small daily slice receives a symbol index.
        self.stock_by_date = self.tables["stock_daily"].set_index("date", drop=False)
        self.fund_by_date = self.tables["fund_daily"].set_index("date", drop=False)

        fundamentals = self.tables["fundamentals"]
        if not pd.api.types.is_datetime64_any_dtype(fundamentals["announcement_date"]):
            fundamentals["announcement_date"] = pd.to_datetime(fundamentals["announcement_date"])
        # Derive the aliases used by the JoinQuant white-horse query from
        # report-period matches. Values are percentage points, matching the
        # source thresholds (1.5, 2, 3 and 20).
        fundamentals = fundamentals.sort_values(["symbol", "report_period", "announcement_date"], kind="stable").copy()
        fundamentals = fundamentals.drop_duplicates(["symbol", "report_period"], keep="last")
        prior = fundamentals[["symbol", "report_period", "revenue", "net_profit"]].copy()
        prior["report_period"] = prior["report_period"] + pd.DateOffset(years=1)
        prior = prior.rename(columns={"revenue": "revenue_prior", "net_profit": "net_profit_prior"})
        fundamentals = fundamentals.merge(prior, on=["symbol", "report_period"], how="left")
        fundamentals["revenue_yoy"] = np.where(
            fundamentals["revenue_prior"].abs() > 0,
            (fundamentals["revenue"] / fundamentals["revenue_prior"] - 1.0) * 100.0,
            np.nan,
        )
        fundamentals["net_profit_yoy"] = np.where(
            fundamentals["net_profit_prior"].abs() > 0,
            (fundamentals["net_profit"] / fundamentals["net_profit_prior"] - 1.0) * 100.0,
            np.nan,
        )
        self.tables["fundamentals"] = fundamentals
        self._fundamentals_sorted = fundamentals.sort_values(["announcement_date", "symbol"], kind="stable").rename(
            columns={"announcement_date": "_source_asof_date"}
        )
        self._fundamental_symbols = self._fundamentals_sorted["symbol"].drop_duplicates().sort_values(kind="stable")

        audit = self.tables["audit_opinion"]
        if not audit.empty:
            if not pd.api.types.is_datetime64_any_dtype(audit["date"]):
                audit["date"] = pd.to_datetime(audit["date"])
            audit = audit[audit["audit_type"].eq("financial_statements")]
            if "quarter" in audit.columns:
                audit = audit[audit["quarter"].astype(str).str.lower().str.endswith("q4")]
            self._audit_sorted = audit.sort_values(["date", "symbol"], kind="stable").rename(
                columns={"date": "_source_asof_date"}
            )
            self._audit_symbols = self._audit_sorted["symbol"].drop_duplicates().sort_values(kind="stable")
        else:
            self._audit_sorted = audit.rename(columns={"date": "_source_asof_date"})
            self._audit_symbols = pd.Series(dtype="object")

    @staticmethod
    def _day(indexed: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
        try:
            day = indexed.loc[date]
        except KeyError:
            return pd.DataFrame()
        if isinstance(day, pd.Series):
            day = day.to_frame().T
        return day.set_index("symbol", drop=False)

    @staticmethod
    def _asof_snapshot(frame: pd.DataFrame, symbols: pd.Series, output_date_column: str, date: pd.Timestamp) -> pd.DataFrame:
        """Return each symbol's latest row strictly before the signal date."""
        if frame.empty:
            return frame.rename(columns={"_source_asof_date": output_date_column}).copy()
        query_key = "_query_asof_date"
        query = pd.DataFrame({"symbol": symbols, query_key: pd.Timestamp(date)})
        query[query_key] = query[query_key].astype(frame["_source_asof_date"].dtype)
        result = pd.merge_asof(
            query,
            frame,
            left_on=query_key,
            right_on="_source_asof_date",
            by="symbol",
            direction="backward",
            allow_exact_matches=False,
        )
        return result.drop(columns=[query_key]).rename(columns={"_source_asof_date": output_date_column}).dropna(subset=[output_date_column])

    @staticmethod
    def _is_rebalance_date(date: pd.Timestamp, next_date: pd.Timestamp | None, frequency: str) -> bool:
        """Return the signal date whose next session is the scheduled review.

        The supplied JoinQuant code uses ``run_weekly(..., 2)`` for the small
        cap sleeve.  With daily OHLC data the defensible equivalent is to
        observe Monday's close and execute Tuesday's open.  This keeps the
        signal strictly point-in-time while preserving the Tuesday cadence.
        """
        if next_date is None:
            return True
        if frequency in {"weekly", "weekly_tuesday"}:
            # Normal weeks: Monday close -> Tuesday open.  The second clause
            # handles a Tuesday holiday by using the last session before the
            # first session after Tuesday in the same short review window.
            if next_date.weekday() == 1:
                return True
            return bool(date.weekday() < 1 < next_date.weekday() and (next_date - date).days <= 4)
        if frequency == "semi_monthly":
            crosses_midmonth = date.day <= 15 < next_date.day and date.month == next_date.month
            return bool(crosses_midmonth or date.month != next_date.month)
        raise ValueError("rebalance_frequency must be 'weekly' or 'semi_monthly'")

    def _sleeve_rebalance_flags(self, date: pd.Timestamp, next_date: pd.Timestamp | None) -> dict[str, bool]:
        """Return sleeve-specific signal dates.

        The supplied JoinQuant program schedules the small-cap sleeve every
        Tuesday and refreshes the white-horse signal monthly.  ETF remains on
        the repository's frozen cadence.  For all non-replica configurations
        the historical global cadence is preserved.
        """
        frequency = str(self.config.get("rebalance", {}).get("frequency", "weekly"))
        if str(self.config.get("small_cap", {}).get("replica_mode", "")).lower() == "joinquant_replica":
            small_due = date.weekday() == 1  # run_weekly(..., 2) in JoinQuant
        else:
            small_due = self._is_rebalance_date(date, next_date, frequency)
        if str(self.config.get("blue_chip", {}).get("replica_mode", "")).lower() == "joinquant_replica":
            blue_due = next_date is None or next_date.month != date.month
        else:
            blue_due = self._is_rebalance_date(date, next_date, frequency)
        etf_due = self._is_rebalance_date(date, next_date, frequency)
        return {"small_cap": small_due, "blue_chip": blue_due, "etf": etf_due}

    def _fundamentals_asof(self, date: pd.Timestamp) -> pd.DataFrame:
        return self._asof_snapshot(self._fundamentals_sorted, self._fundamental_symbols, "announcement_date", date)

    def _audit_asof(self, date: pd.Timestamp) -> pd.DataFrame:
        return self._asof_snapshot(self._audit_sorted, self._audit_symbols, "date", date)

    def run(self, mode: str = "adaptive", cost_multiplier: float = 1.0) -> BacktestResult:
        if mode != "adaptive":
            raise ValueError("mode must be adaptive")
        run_cfg = self.config["run"]
        start, end = pd.Timestamp(run_cfg["start_date"]), pd.Timestamp(run_cfg["effective_end_date"])
        dates = self.regimes.loc[self.regimes["date"].between(start, end), "date"].tolist()
        active_sleeves = set(run_cfg.get("active_sleeves", ["small_cap", "etf", "blue_chip"]))
        invalid_sleeves = active_sleeves - {"small_cap", "etf", "blue_chip"}
        if invalid_sleeves:
            raise ValueError(f"unknown active sleeves: {sorted(invalid_sleeves)}")
        if not dates:
            raise ValueError("benchmark has no dates in configured backtest period")

        cash = float(run_cfg["initial_cash"])
        positions: dict[str, Position] = {}
        pending_targets: dict[str, tuple[float, str]] | None = None
        pending_signal_date = None
        self._forced_signal_dates = {}
        forced_sells: dict[str, str] = {}
        sleeve_universes = {"small_cap": [], "etf": [], "blue_chip": []}
        risk = RiskOverlay(self.config["risk"])
        previous_risk = previous_regime = None
        records: dict[str, list[dict]] = {key: [] for key in ("equity", "trades", "holdings", "contributions", "evidence", "blocked", "allocations")}
        last_prices: dict[str, float] = {}
        previous_sleeve_values = {name: 0.0 for name in sleeve_universes}
        # The pasted JoinQuant strategy keeps a portfolio-level monthly loss
        # ledger in addition to sleeve-specific stops.  Keep this state local
        # to a run so repeated backtests remain deterministic.
        self._monthly_key = None
        self._monthly_realized_loss = 0.0
        self._monthly_stop_trading = False
        self._white_horse_signal_cache = {}

        for index, date in enumerate(dates):
            stock_day = self._day(self.stock_by_date, date)
            fund_day = self._day(self.fund_by_date, date)
            trade_start = len(records["trades"])
            if pending_targets is not None or forced_sells:
                blocked_start = len(records["blocked"])
                cash = self._execute(date, cash, positions, pending_targets, forced_sells, stock_day, fund_day,
                                     records, cost_multiplier, pending_signal_date, last_prices)
                # A normal rebalance is attempted once on its scheduled date.
                # Otherwise a T+1 / limit block would retry the entire target
                # book on every subsequent day and silently defeat the selected
                # weekly or half-monthly cadence.  Stop exits remain in
                # ``forced_sells`` and are retried until executable.
                forced_sells = {symbol: reason for symbol, reason in forced_sells.items() if symbol in positions}
                pending_targets = None

            open_nav = cash + self._positions_value(positions, stock_day, fund_day, last_prices, "open")
            close_nav = cash + self._positions_value(positions, stock_day, fund_day, last_prices, "close")
            for symbol, position in positions.items():
                row = self._row(symbol, stock_day, fund_day)
                if row is not None:
                    last_prices[symbol] = self._adjusted(row, "close", position.sleeve)
                    position.peak_close = max(position.peak_close, last_prices[symbol])
            risk_tier, drawdown = risk.update(close_nav)
            regime = str(self.regime_by_date.get(date, "warm"))
            records["equity"].append({"date": date, "open_nav": open_nav, "nav": close_nav, "cash": cash, "drawdown": -drawdown, "risk_tier": risk_tier, "regime": regime})

            sleeve_values = {name: 0.0 for name in sleeve_universes}
            for position in positions.values():
                sleeve_values[position.sleeve] += position.shares * last_prices.get(position.symbol, position.average_cost)
                records["holdings"].append({"date": date, "symbol": position.symbol,
                                            "name": position.name_asof_signal or self.symbol_names.get(position.symbol, ""),
                                            "name_asof_signal": position.name_asof_signal or self.symbol_names.get(position.symbol, ""),
                                            "name_current": self.symbol_names.get(position.symbol, ""),
                                            "name_source": position.name_source or "security_master_current",
                                            "sleeve": position.sleeve, "shares": position.shares,
                                            "close": last_prices.get(position.symbol), "market_value": position.shares * last_prices.get(position.symbol, position.average_cost)})
            day_trades = records["trades"][trade_start:]
            contribution = {"date": date}
            for sleeve in sleeve_universes:
                sleeve_trades = [trade for trade in day_trades if trade["sleeve"] == sleeve]
                net_buy = sum((1 if trade["side"] == "buy" else -1) * trade["gross"] for trade in sleeve_trades)
                costs = sum(trade["cost"] for trade in sleeve_trades)
                contribution[sleeve] = sleeve_values[sleeve] - previous_sleeve_values[sleeve] - net_buy - costs
                contribution[f"{sleeve}_value"] = sleeve_values[sleeve]
            contribution["total_pnl"] = sum(contribution[name] for name in sleeve_universes)
            records["contributions"].append(contribution)
            previous_sleeve_values = sleeve_values

            self._evaluate_global_stops(date, positions, stock_day, fund_day, close_nav, forced_sells, records)
            self._evaluate_stops(date, positions, stock_day, fund_day, forced_sells, records)
            next_date = dates[index + 1] if index + 1 < len(dates) else None
            # Each replica sleeve follows the cadence in the supplied
            # JoinQuant program (small-cap Tuesday, white-horse month-end),
            # while ETF retains the frozen repository cadence.  The first
            # session always establishes an initial book.
            due = self._sleeve_rebalance_flags(date, next_date)
            if index == 0:
                due = {name: name in active_sleeves for name in due}
            rebalance_date = any(due.get(name, False) for name in active_sleeves)
            fundamentals_pit = None
            if due["small_cap"] and not self._monthly_stop_trading and "small_cap" in active_sleeves:
                key = ("small_cap", date, config_hash(self.config["small_cap"]))
                if key not in self._selection_cache:
                    fundamentals_pit = self._fundamentals_asof(date)
                    small_cfg = copy.deepcopy(self.config["small_cap"])
                    if small_cfg.get("use_index_universe", False):
                        small_cfg["universe_symbols"] = self._index_constituents(
                            str(small_cfg.get("index_symbol", "399100.SZ")), date
                        )
                        small_cfg["preselect_float_cap_count"] = int(small_cfg.get("preselect_float_cap_count", 200))
                        small_cfg["preselect_total_cap_count"] = int(small_cfg.get("preselect_total_cap_count", 100))
                    small_cfg["float_shares_asof"] = self._float_shares_asof(date, small_cfg.get("universe_symbols"))
                    small_cfg["industry_by_symbol"] = self.symbol_industries
                    self._selection_cache[key] = select_small_cap(
                        date, stock_day.reset_index(drop=True), fundamentals_pit, self._audit_asof(date),
                        self.tables["dividend"], self._index_history(date, small_cfg.get("index_symbol")), small_cfg)
                selection = self._selection_cache[key]
                small_count = int(self.config["small_cap"].get("stock_num", 2))
                if self.config["small_cap"].get("dynamic_holdings", False):
                    small_count = holding_count_from_deviation(
                        self._index_history(date, self.config["small_cap"].get("index_symbol")),
                        self.config["small_cap"],
                    )[0]
                skip_month = pd.Timestamp(date).month in {int(x) for x in self.config["small_cap"].get("skip_months", [])}
                if skip_month:
                    # The source strategy parks the small-cap sleeve in money
                    # market ETFs during January and April.  These symbols are
                    # deliberately kept under the small-cap sleeve so the
                    # 40% structural budget is not silently reassigned.
                    fallback = [
                        symbol for symbol in self.config["small_cap"].get("no_trade_holdings", [])
                        if symbol in set(fund_day.index.astype(str))
                    ]
                    sleeve_universes["small_cap"] = fallback
                else:
                    sleeve_universes["small_cap"] = self._retain_candidate_pool(
                        selection.symbols, positions, "small_cap", small_count
                    )
                self._record_evidence(date, "small_cap", selection.evidence, records, selection.symbols)
            if due["etf"] and not self._monthly_stop_trading and "etf" in active_sleeves:
                key = ("etf", date, config_hash(self.config["etf"]))
                if key not in self._selection_cache:
                    self._selection_cache[key] = select_etfs(date, self.tables["fund_daily"], self.config["etf"])
                selection = self._selection_cache[key]
                sleeve_universes["etf"] = selection.symbols
                self._record_evidence(date, "etf", selection.evidence, records, selection.symbols)
            # Keep a base copy available even when the blue-chip sleeve is not
            # due for a fresh selection on this session.  The candidate pool
            # is carried forward between scheduled reviews, but the execution
            # branch below still needs the replica-mode flags to apply the
            # correct ETF fallback/filtering logic.
            blue_cfg = copy.deepcopy(self.config["blue_chip"])
            if due["blue_chip"] and not self._monthly_stop_trading and "blue_chip" in active_sleeves:
                key = ("blue_chip", date, regime, config_hash(self.config["blue_chip"]))
                if key not in self._selection_cache:
                    if fundamentals_pit is None:
                        fundamentals_pit = self._fundamentals_asof(date)
                    if blue_cfg.get("use_index_universe", False):
                        month_key = pd.Timestamp(date).to_period("M")
                        if month_key not in self._white_horse_signal_cache:
                            self._white_horse_signal_cache[month_key] = self._white_horse_signal(date)
                        signal = self._white_horse_signal_cache[month_key]
                        blue_cfg["white_horse_signal"] = signal
                        blue_sentiment = self._white_horse_market_temp(date)
                        index_symbol = {
                            "big": "000300.SH",
                            "small": "399101.SZ",
                        }.get(signal)
                        universe = self._index_constituents(index_symbol, date) if index_symbol else []
                        if index_symbol and universe:
                            # JoinQuant first narrows to the CSI300 largest-20
                            # or ChiNext smallest-20 by circulating value,
                            # then applies the state-dependent fundamentals.
                            # ``stock_day`` is indexed by symbol for execution;
                            # reset it before a point-in-time universe merge so
                            # Pandas does not treat ``symbol`` as both index and
                            # column.
                            latest = stock_day[stock_day["symbol"].isin(universe)].reset_index(drop=True).copy()
                            pit = fundamentals_pit[fundamentals_pit["symbol"].isin(universe)].copy()
                            if not latest.empty and not pit.empty:
                                latest = latest.merge(
                                    pit[["symbol", "total_shares"]], on="symbol", how="left", suffixes=("", "_fund")
                                )
                                float_map = self._float_shares_asof(date, universe)
                                latest["float_shares"] = latest["symbol"].map(float_map)
                                latest["float_shares"] = latest["float_shares"].fillna(latest["total_shares"])
                                latest["circulating_market_cap"] = latest["close"] * latest["float_shares"]
                                latest = latest.dropna(subset=["circulating_market_cap"])
                                ascending = signal == "small"
                                universe = latest.sort_values(
                                    ["circulating_market_cap", "symbol"], ascending=[ascending, True]
                                ).head(20)["symbol"].astype(str).tolist()
                        blue_cfg["universe_symbols"] = universe or []
                        blue_cfg["float_shares_asof"] = self._float_shares_asof(date, blue_cfg.get("universe_symbols"))
                    if blue_cfg.get("white_horse_signal") == "etf":
                        # The source code holds a static foreign-ETF fallback.
                        # Only symbols with an actual point-in-time daily row
                        # are eligible; unavailable listings are reported as
                        # unavailable rather than backfilled.
                        available = [
                            symbol for symbol in blue_cfg.get("foreign_etfs", [])
                            if symbol in set(self.tables["fund_daily"]["symbol"].astype(str))
                            and symbol in set(fund_day.index.astype(str))
                        ]
                        fallback = available[: int(blue_cfg.get("stock_num", 5)) * 2]
                        evidence = pd.DataFrame({"symbol": fallback, "eligible": True, "market_state": blue_sentiment})
                        self._selection_cache[key] = Selection(fallback, evidence)
                    else:
                        self._selection_cache[key] = select_blue_chips(
                            date, stock_day.reset_index(drop=True), fundamentals_pit,
                            blue_sentiment if blue_cfg.get("use_index_universe", False) else regime,
                            blue_cfg)
                selection = self._selection_cache[key]
                blue_count = int(self.config["blue_chip"].get("stock_num", 5))
                if self.config["blue_chip"].get("dynamic_holdings", False):
                    target_counts = pd.to_numeric(selection.evidence.get("target_count", pd.Series(dtype=float)), errors="coerce").dropna()
                    if not target_counts.empty:
                        blue_count = int(target_counts.iloc[0])
                blue_symbols = [symbol for symbol in selection.symbols if symbol not in sleeve_universes["small_cap"]]
                if blue_cfg.get("replica_mode") == "joinquant_replica" and blue_cfg.get("white_horse_signal") == "etf":
                    blue_symbols = [symbol for symbol in blue_cfg.get("foreign_etfs", [])
                                    if symbol in set(fund_day.index.astype(str))]
                sleeve_universes["blue_chip"] = self._retain_candidate_pool(
                    blue_symbols, positions, "blue_chip", blue_count,
                )
                self._record_evidence(date, "blue_chip", selection.evidence, records, selection.symbols)
            if rebalance_date:
                weights = target_allocation(mode, regime, self.config["allocation"], risk_tier, self.config["risk"])
                weights, budget_evidence = self._apply_sleeve_risk_budget(
                    weights, sleeve_universes, stock_day, fund_day
                )
                if budget_evidence:
                    records["evidence"].extend(
                        {"date": date, "event": "risk_budget", **row} for row in budget_evidence
                    )
                pending_targets = self._expand_targets(weights, sleeve_universes)
                pending_signal_date = date
                records["allocations"].append({
                    "date": date, "mode": mode, "regime": regime, "risk_tier": risk_tier,
                    "small_cap": weights.get("small_cap", 0.0), "etf": weights.get("etf", 0.0),
                    "blue_chip": weights.get("blue_chip", 0.0), "cash": weights.get("cash", 0.0),
                    "small_cap_count": len(sleeve_universes["small_cap"]),
                    "etf_count": len(sleeve_universes["etf"]),
                    "blue_chip_count": len(sleeve_universes["blue_chip"]),
                })
            previous_regime, previous_risk = regime, risk_tier

        equity = pd.DataFrame(records["equity"])
        benchmark = self._benchmark_series(dates)
        equity["benchmark"] = equity["date"].map(benchmark)
        metadata = {"mode": mode, "cost_multiplier": cost_multiplier, "config_hash": config_hash(self.config),
                    "start_date": str(dates[0].date()), "end_date": str(dates[-1].date()), "initial_cash": run_cfg["initial_cash"]}
        return BacktestResult(mode, equity, pd.DataFrame(records["trades"]), pd.DataFrame(records["holdings"]), self.regimes,
                              pd.DataFrame(records["contributions"]), pd.DataFrame(records["evidence"]), pd.DataFrame(records["blocked"]),
                              pd.DataFrame(records["allocations"]), metadata)

    def _execute(self, date, cash, positions, targets, forced_sells, stock_day, fund_day, records, cost_multiplier,
                 signal_date=None, last_prices=None):
        rebalance = targets is not None
        targets = targets or {}
        last_prices = last_prices or {}
        prices = {}
        for symbol in set(targets) | set(positions):
            sleeve = targets.get(symbol, (0, positions.get(symbol, Position(symbol, "small_cap", 0, 0, date, 0)).sleeve))[1]
            row = self._row(symbol, stock_day, fund_day)
            if row is not None:
                prices[symbol] = self._adjusted(row, "open", sleeve)
        nav = cash + sum(position.shares * prices.get(symbol, last_prices.get(symbol, position.average_cost)) for symbol, position in positions.items())
        desired = {symbol: int(nav * weight / prices[symbol] // 100 * 100) for symbol, (weight, _) in targets.items() if symbol in prices and prices[symbol] > 0}
        if not rebalance:
            desired = {symbol: position.shares for symbol, position in positions.items()}
        for symbol in forced_sells:
            desired[symbol] = 0
        deltas = {symbol: desired.get(symbol, 0) - position.shares for symbol, position in positions.items()}
        deltas.update({symbol: shares for symbol, shares in desired.items() if symbol not in positions})
        for side in ("sell", "buy"):
            for symbol in sorted(deltas):
                delta = deltas[symbol]
                if (side == "sell" and delta >= 0) or (side == "buy" and delta <= 0):
                    continue
                row = self._row(symbol, stock_day, fund_day)
                sleeve = targets.get(symbol, (0, positions[symbol].sleeve if symbol in positions else "small_cap"))[1]
                reason = self._blocked_reason(side, row)
                if side == "sell" and symbol in positions and positions[symbol].acquired_date >= date:
                    reason = "t_plus_one"
                if reason:
                    records["blocked"].append({"date": date, "symbol": symbol, "side": side, "shares": abs(delta), "reason": reason})
                    continue
                price = prices[symbol]
                shares = min(abs(delta), positions[symbol].shares) if side == "sell" else abs(delta)
                is_cash_proxy = symbol in set(self.config["small_cap"].get("no_trade_holdings", []))
                bps = self.config["costs"][f"{'etf' if sleeve == 'etf' or is_cash_proxy else 'stock'}_{side}_bps"] * cost_multiplier
                gross = shares * price
                cost = gross * float(bps) / 10000
                if side == "buy" and gross + cost > cash:
                    shares = int(cash / (price * (1 + float(bps) / 10000)) // 100 * 100)
                    gross, cost = shares * price, shares * price * float(bps) / 10000
                if shares <= 0:
                    continue
                if side == "sell":
                    cash += gross - cost
                    position = positions[symbol]
                    # Portfolio-level monthly loss accounting mirrors the
                    # source strategy's realized-loss ledger.  Costs are
                    # already separately reported and are not double counted.
                    realized = (price - position.average_cost) * shares
                    if realized < 0:
                        self._monthly_realized_loss += float(realized)
                    position.shares -= shares
                    if position.shares == 0:
                        del positions[symbol]
                else:
                    cash -= gross + cost
                    if symbol in positions:
                        position = positions[symbol]
                        position.average_cost = (position.average_cost * position.shares + gross) / (position.shares + shares)
                        position.shares += shares
                    else:
                        signal_name, name_source = self._signal_name(symbol, row, signal_date)
                        positions[symbol] = Position(symbol, sleeve, shares, price, date, price,
                                                     signal_name, name_source)
                order_signal_date = getattr(self, "_forced_signal_dates", {}).get(symbol, signal_date) if symbol in forced_sells else signal_date
                records["trades"].append({"date": date, "signal_date": order_signal_date, "symbol": symbol, "sleeve": sleeve, "side": side, "shares": shares,
                                           "price": price, "gross": gross, "cost": cost, "reason": forced_sells.get(symbol, "rebalance")})
                if side == "sell" and symbol not in positions:
                    getattr(self, "_forced_signal_dates", {}).pop(symbol, None)
        return cash

    def _evaluate_global_stops(self, date, positions, stock_day, fund_day, nav, forced_sells, records):
        """Apply the copied strategy's 2% single-loss / 6% monthly overlay.

        Daily OHLC cannot reproduce the four intraday callbacks exactly.  We
        therefore evaluate the close and submit a next-open signal, matching
        the rest of this backtest's point-in-time execution convention.
        """
        month_key = pd.Timestamp(date).to_period("M")
        if self._monthly_key != month_key:
            self._monthly_key = month_key
            self._monthly_realized_loss = 0.0
            self._monthly_stop_trading = False

        stop_cfg = self.config.get("global_stop_loss", {})
        single_ratio = float(stop_cfg.get("single_loss_ratio", 0.02))
        monthly_ratio = float(stop_cfg.get("monthly_loss_ratio", 0.06))
        if not positions or nav <= 0:
            return

        unrealized_loss = 0.0
        for symbol, position in list(positions.items()):
            row = self._row(symbol, stock_day, fund_day)
            if row is None:
                continue
            close = self._adjusted(row, "close", position.sleeve)
            pnl = (close - position.average_cost) * position.shares
            if pnl < 0:
                unrealized_loss += float(pnl)
                if abs(pnl) >= nav * single_ratio:
                    forced_sells.setdefault(symbol, "stop_global_single")
                    self._forced_signal_dates.setdefault(symbol, date)
                    records["evidence"].append({
                        "date": date, "sleeve": position.sleeve, "symbol": symbol,
                        "event": "stop_signal", "eligible": False,
                        "close": close, "stop_price": np.nan,
                        "reason": "global_single_loss",
                    })

        monthly_loss = float(self._monthly_realized_loss + unrealized_loss)
        if monthly_loss <= -nav * monthly_ratio:
            self._monthly_stop_trading = True
            records["evidence"].append({
                "date": date, "event": "global_monthly_stop", "eligible": False,
                "monthly_loss": monthly_loss, "threshold": -nav * monthly_ratio,
                "reason": "global_monthly_loss",
            })
            for symbol in positions:
                forced_sells.setdefault(symbol, "stop_global_monthly")
                self._forced_signal_dates.setdefault(symbol, date)

    def _evaluate_stops(self, date, positions, stock_day, fund_day, forced_sells, records):
        # The JoinQuant source checks the CSI1000 market ratio intraday.  With
        # daily OHLC data the earliest defensible equivalent is a close/open
        # signal observed at today's close and executed next session.
        small_cfg = self.config["small_cap"]
        market_stop = small_cfg.get("market_stoploss")
        if market_stop is not None:
            index_symbol = str(small_cfg.get("index_symbol", "399100.SZ"))
            index_day = self.tables["index_daily"]
            index_day = index_day[(index_day["symbol"] == index_symbol) & index_day["date"].eq(date)]
            if not index_day.empty:
                row = index_day.iloc[-1]
                market_ratio = float(row["close"]) / float(row["open"]) if float(row["open"]) > 0 else np.nan
                if np.isfinite(market_ratio) and market_ratio <= float(market_stop):
                    for symbol, position in list(positions.items()):
                        if position.sleeve == "small_cap" and symbol in stock_day.index:
                            forced_sells[symbol] = "stop_market"
                            self._forced_signal_dates.setdefault(symbol, date)
                            records["evidence"].append({
                                "date": date, "sleeve": "small_cap", "symbol": symbol,
                                "event": "stop_signal", "eligible": False,
                                "market_ratio": market_ratio, "stop_price": np.nan,
                                "reason": "market",
                            })
        for symbol, position in list(positions.items()):
            if symbol in set(small_cfg.get("no_trade_holdings", [])):
                # Money-market parking positions are the sleeve's explicit
                # January/April cash proxy and have no equity stop rule.
                continue
            if position.sleeve == "small_cap" and symbol in stock_day.index:
                row = stock_day.loc[symbol]
                atr = float(row.get("atr14", np.nan))
                close = self._adjusted(row, "close", "small_cap")
                triggered, stop_price, reason = small_cap_stop(
                    position.average_cost, position.peak_close, close, atr, self.config["small_cap"]
                )
            elif position.sleeve == "etf" and symbol in fund_day.index:
                row = fund_day.loc[symbol]
                close = self._adjusted(row, "close", "etf")
                triggered, stop_price, reason = etf_stop(
                    position.average_cost, position.peak_close, close, self.config["etf"], float(row.get("ret3", np.nan))
                )
            elif position.sleeve == "blue_chip" and symbol in stock_day.index:
                row = stock_day.loc[symbol]
                close = self._adjusted(row, "close", "blue_chip")
                triggered, stop_price, reason = blue_chip_stop(
                    position.average_cost, close, self.config["blue_chip"]
                )
            else:
                continue
            if triggered:
                forced_sells[symbol] = f"stop_{reason}"
                self._forced_signal_dates.setdefault(symbol, date)
                records["evidence"].append({"date": date, "sleeve": position.sleeve, "symbol": symbol, "event": "stop_signal", "eligible": False,
                                            "close": close, "stop_price": stop_price, "reason": reason})

    def _apply_sleeve_risk_budget(self, weights, universes, stock_day, fund_day):
        """Apply point-in-time sleeve risk budgets and release unused capital to cash.

        The default method preserves the historical absolute-volatility scaling.
        ``relative_inverse_vol`` turns the configured regime weights into risk
        shares, divides them by each sleeve's observable volatility proxy, and
        normalizes the resulting capital weights inside the current investable
        budget. Missing or empty sleeves are never subsidized with invented risk.
        """
        adjusted = dict(weights)
        evidence = []
        risk_cfg = self.config.get("sleeve_risk_budget", {})
        method = risk_cfg.get("method", "absolute_target_vol")
        if method == "fixed":
            # Risk budgets are deliberately internal to each sleeve in the
            # structural portfolio.  Do not convert volatility into a
            # cross-sleeve allocation change here.
            return dict(weights), [{"sleeve": sleeve, "method": method,
                                    "target_volatility": np.nan,
                                    "realized_volatility_proxy": np.nan,
                                    "scale": 1.0,
                                    "weight_before": float(weights.get(sleeve, 0.0)),
                                    "weight_after": float(weights.get(sleeve, 0.0))}
                                   for sleeve in ("small_cap", "etf", "blue_chip")]
        observed: dict[str, float] = {}
        for sleeve in ("small_cap", "etf", "blue_chip"):
            source = fund_day if sleeve == "etf" else stock_day
            values = []
            for symbol in universes[sleeve]:
                if symbol in source.index:
                    row = source.loc[symbol]
                    if isinstance(row, pd.DataFrame):
                        row = row.iloc[-1]
                    value = float(row.get("volatility", np.nan))
                    if np.isfinite(value) and value > 0:
                        values.append(value)
            observed[sleeve] = float(np.mean(values)) if values else np.nan

        if method == "relative_inverse_vol":
            investable = sum(float(adjusted.get(sleeve, 0.0)) for sleeve in ("small_cap", "etf", "blue_chip"))
            floor = float(risk_cfg.get("volatility_floor", 0.05))
            min_scale = float(risk_cfg.get("min_scale", 0.25))
            max_scale = float(risk_cfg.get("max_scale", 1.75))
            raw = {}
            for sleeve in ("small_cap", "etf", "blue_chip"):
                base = float(adjusted.get(sleeve, 0.0))
                vol = observed[sleeve]
                if base > 0 and universes[sleeve] and np.isfinite(vol):
                    raw[sleeve] = base / max(vol, floor)
            denominator = sum(raw.values())
            proposed = {sleeve: (investable * raw.get(sleeve, 0.0) / denominator if denominator else 0.0)
                        for sleeve in ("small_cap", "etf", "blue_chip")}
            released = 0.0
            for sleeve in proposed:
                before = float(adjusted.get(sleeve, 0.0))
                if before <= 0:
                    after = 0.0
                    scale = 0.0
                else:
                    lower, upper = before * min_scale, before * max_scale
                    after = min(max(proposed[sleeve], lower), upper) if universes[sleeve] else 0.0
                    scale = after / before
                adjusted[sleeve] = after
                released += before - after
                evidence.append({"sleeve": sleeve, "method": method, "target_volatility": np.nan,
                                 "realized_volatility_proxy": observed[sleeve], "scale": scale,
                                 "weight_before": before, "weight_after": after})
            adjusted["cash"] = float(adjusted.get("cash", 0.0)) + released
            return adjusted, evidence

        released = 0.0
        for sleeve in ("small_cap", "etf", "blue_chip"):
            cfg = self.config[sleeve]
            target = cfg.get("target_volatility")
            if target is None or adjusted.get(sleeve, 0.0) <= 0:
                continue
            realized = observed[sleeve]
            scale = min(float(cfg.get("risk_budget_max_scale", 1.0)), float(target) / realized) if np.isfinite(realized) else 1.0
            scale = max(float(cfg.get("risk_budget_min_scale", 0.0)), scale)
            original = adjusted[sleeve]
            adjusted[sleeve] = original * scale
            released += original - adjusted[sleeve]
            evidence.append({"sleeve": sleeve, "method": method, "target_volatility": float(target),
                             "realized_volatility_proxy": realized, "scale": scale,
                             "weight_before": original, "weight_after": adjusted[sleeve]})
        adjusted["cash"] = adjusted.get("cash", 0.0) + released
        return adjusted, evidence

    def _expand_targets(self, weights, universes):
        result = {}
        for sleeve in ("small_cap", "blue_chip"):
            symbols = universes[sleeve]
            if symbols:
                result.update({symbol: (weights[sleeve] / len(symbols), sleeve) for symbol in symbols})
        etfs = universes["etf"]
        if etfs:
            slots = len(etfs) if self.config["etf"].get("full_budget_when_fewer", False) else int(self.config["etf"]["max_positions"])
            result.update({symbol: (weights["etf"] / max(slots, 1), "etf") for symbol in etfs})
        return result

    @staticmethod
    def _retain_candidate_pool(pool: list[str], positions: dict[str, Position], sleeve: str, count: int) -> list[str]:
        """Keep existing names inside the source strategy's 2x candidate pool."""
        if not pool or count <= 0:
            return []
        retained = [symbol for symbol in pool if symbol in positions and positions[symbol].sleeve == sleeve]
        additions = [symbol for symbol in pool if symbol not in retained]
        return (retained + additions)[:max(count, 1)]

    @staticmethod
    def _record_evidence(date, sleeve, evidence, records, selected):
        if evidence.empty:
            records["evidence"].append({"date": date, "sleeve": sleeve, "event": "selection_summary", "candidates": 0,
                                        "eligible_count": 0, "selected_count": 0})
            return
        # Keep the complete ETF candidate pool so an empty selection remains
        # diagnosable (the prior report retained selected rows only).
        copy = evidence.copy()
        if "date" in copy.columns:
            copy = copy.rename(columns={"date": "source_date"})
        if "symbol" in copy:
            if sleeve != "etf":
                copy = copy[copy["symbol"].isin(selected)].copy()
            copy["selected"] = copy["symbol"].isin(selected)
            if "eligible" in copy.columns:
                condition_cols = [c for c in (
                    "three_day_ok", "score_ok", "momentum_ok", "above_ma_ok",
                    "ma_order_ok", "amount_ok", "relative_strength_ok"
                ) if c in copy.columns]
                if condition_cols:
                    copy["failure_reasons"] = copy.apply(
                        lambda row: ",".join(c.removesuffix("_ok") for c in condition_cols if row.get(c) is False),
                        axis=1,
                    )
        copy.insert(0, "sleeve", sleeve)
        copy.insert(0, "date", date)
        copy["event"] = "selection"
        records["evidence"].extend(copy.to_dict("records"))
        eligible = evidence.get("eligible", pd.Series(False, index=evidence.index)).fillna(False).astype(bool)
        records["evidence"].append({"date": date, "sleeve": sleeve, "event": "selection_summary",
                                    "candidates": len(evidence), "eligible_count": int(eligible.sum()),
                                    "selected_count": len(selected)})

    @staticmethod
    def _row(symbol, stock_day, fund_day):
        source = fund_day if symbol in fund_day.index else stock_day
        if symbol not in source.index:
            return None
        row = source.loc[symbol]
        return row.iloc[-1] if isinstance(row, pd.DataFrame) else row

    def _signal_name(self, symbol: str, row: pd.Series | None, signal_date=None) -> tuple[str, str]:
        """Resolve a name as-of the execution/signal snapshot.

        ``stock_daily.name`` is the only point-in-time label available in the
        cache. If a row is unavailable (most commonly for ETFs), fall back to
        the current security master and label that fallback explicitly.
        """
        signal_row = row
        if signal_date is not None and symbol in self.stock_by_date.index:
            snapshot = self._day(self.stock_by_date, pd.Timestamp(signal_date))
            if symbol in snapshot.index:
                signal_row = snapshot.loc[symbol]
                if isinstance(signal_row, pd.DataFrame):
                    signal_row = signal_row.iloc[-1]
        if signal_row is not None and "name" in signal_row.index:
            value = signal_row.get("name")
            if pd.notna(value) and str(value).strip():
                return str(value).strip(), "stock_daily_asof_signal"
        current = self.symbol_names.get(str(symbol), "")
        return str(current), "security_master_current"

    @staticmethod
    def _adjusted(row, field, sleeve):
        key = f"adj_{field}" if f"adj_{field}" in row.index else field
        return float(row[key])

    @staticmethod
    def _blocked_reason(side, row):
        if row is None or pd.isna(row.get("open")) or row.get("open", 0) <= 0:
            return "missing_open"
        if int(row.get("trade_status", 0) or 0) != 0:
            return "suspended"
        if side == "buy" and pd.notna(row.get("limit_up")) and row["open"] >= row["limit_up"]:
            return "limit_up"
        if side == "sell" and pd.notna(row.get("limit_down")) and row["open"] <= row["limit_down"]:
            return "limit_down"
        return None

    def _positions_value(self, positions, stock_day, fund_day, last_prices, field):
        total = 0.0
        for symbol, position in positions.items():
            row = self._row(symbol, stock_day, fund_day)
            price = self._adjusted(row, field, position.sleeve) if row is not None else last_prices.get(symbol, position.average_cost)
            total += position.shares * price
        return total

    def _float_shares_asof(self, date: pd.Timestamp, symbols: list[str] | None = None) -> dict[str, float]:
        if self.float_reference.empty:
            return {}
        query_symbols = symbols or self.float_reference["symbol"].drop_duplicates().tolist()
        query = pd.DataFrame({"symbol": query_symbols, "date": pd.Timestamp(date)})
        query = query.sort_values(["date", "symbol"], kind="stable")
        merged = pd.merge_asof(
            query,
            self.float_reference,
            on="date",
            by="symbol",
            direction="backward",
            allow_exact_matches=True,
        )
        value_column = "free_circulation" if "free_circulation" in merged.columns else "float_shares"
        if value_column not in merged.columns:
            return {}
        values: dict[str, float] = {}
        for row in merged.itertuples(index=False):
            value = float(getattr(row, value_column, np.nan))
            if np.isfinite(value) and value > 0:
                values[str(row.symbol)] = value
        return values

    def _index_history(self, date, symbol=None):
        symbol = str(symbol or self.config["run"]["benchmark"])
        return self.tables["index_daily"][(self.tables["index_daily"]["symbol"] == symbol) & (self.tables["index_daily"]["date"] <= date)]

    def _index_constituents(self, index_symbol: str, date: pd.Timestamp) -> list[str] | None:
        frame = self.index_weights.get(index_symbol)
        if frame is None or frame.empty:
            return None
        # Constituents are looked up as of the previous completed session.
        # This avoids consuming a monthly index snapshot that was published at
        # the signal date's close.
        available = frame.loc[frame["date"] < pd.Timestamp(date), "date"]
        if available.empty:
            return None
        snapshot_date = available.max()
        return sorted(frame.loc[frame["date"].eq(snapshot_date), "stock_symbol"].astype(str).unique().tolist())

    def _white_horse_signal(self, date: pd.Timestamp) -> str:
        """Replicate the pasted JoinQuant 10-day big-v-small signal."""
        returns: dict[str, float] = {}
        for symbol in ("000300.SH", "399101.SZ"):
            frame = self.tables["index_daily"]
            frame = frame[(frame["symbol"] == symbol) & (frame["date"] <= date)].sort_values("date").tail(10)
            if len(frame) >= 2:
                returns[symbol] = float(frame["close"].iloc[-1] / frame["close"].iloc[0] - 1.0)
            else:
                returns[symbol] = 0.0
        big_ret, small_ret = returns.get("000300.SH", 0.0), returns.get("399101.SZ", 0.0)
        if big_ret > small_ret and big_ret > 0:
            return "big" if big_ret <= 0.05 else "small"
        if small_ret > big_ret and small_ret > 0:
            return "small"
        return "etf"

    def _white_horse_market_temp(self, date: pd.Timestamp) -> str:
        """JoinQuant ``assess_market_temp`` using the CSI300 history."""
        frame = self.tables["index_daily"]
        frame = frame[(frame["symbol"] == "000300.SH") & (frame["date"] <= date)].sort_values("date").tail(220)
        if len(frame) < 200:
            return "warm"
        closes = frame["close"].astype(float).to_numpy()
        span = float(closes.max() - closes.min())
        if span <= 0:
            return "warm"
        height = (float(closes[-5:].mean()) - float(closes.min())) / span
        if height < 0.20:
            return "cold"
        if height > 0.90:
            return "overheated"
        return "warm"

    def _benchmark_series(self, dates):
        frame = self._index_history(dates[-1]).set_index("date")["close"].reindex(dates).ffill()
        return (frame / frame.iloc[0]).to_dict()


