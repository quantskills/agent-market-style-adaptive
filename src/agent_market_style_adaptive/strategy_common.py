from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

@dataclass(frozen=True)
class Selection:
    symbols: list[str]
    evidence: pd.DataFrame

def _as_date(value) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()

def point_in_time_latest(
    frame: pd.DataFrame, as_of, date_column: str = "announcement_date", group_column: str = "symbol"
) -> pd.DataFrame:
    """Latest row strictly before signal date, enforcing next-trading-day availability."""
    if frame.empty:
        return frame.copy()
    data = frame.copy()
    data[date_column] = pd.to_datetime(data[date_column])
    data = data[data[date_column] < _as_date(as_of)]
    return data.sort_values([group_column, date_column]).groupby(group_column, as_index=False).tail(1)

def _tradable_snapshot(frame: pd.DataFrame, as_of=None) -> pd.Series:
    status = frame.get("trade_status", pd.Series(0, index=frame.index)).fillna(1)
    close = frame.get("close", pd.Series(np.nan, index=frame.index))
    tradable = (status == 0) & close.notna() & (close > 0)
    if "delist_date" in frame.columns and as_of is not None:
        delist = pd.to_datetime(frame["delist_date"], errors="coerce")
        tradable &= delist.isna() | (delist > _as_date(as_of))
    return tradable
