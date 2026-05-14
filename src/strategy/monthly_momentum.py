"""
Simple monthly ETF momentum rotation.

At each month end, hold the ETF with the strongest trailing return.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MonthlyMomentumSignal:
    date: pd.Timestamp
    weights: dict[str, float]
    returns: dict[str, float]


def generate_monthly_momentum_signals(
    price_data: dict[str, pd.DataFrame],
    universe: list[str],
    lookback_months: int = 1,
) -> list[MonthlyMomentumSignal]:
    monthly = {
        sym: price_data[sym]["close"].resample("ME").last()
        for sym in universe
        if sym in price_data and "close" in price_data[sym]
    }
    if not monthly:
        return []

    close = pd.DataFrame(monthly).dropna()
    mom = close.pct_change(lookback_months)
    signals: list[MonthlyMomentumSignal] = []
    for date, row in mom.iloc[lookback_months:].iterrows():
        valid = {sym: float(row[sym]) for sym in close.columns if pd.notna(row[sym])}
        if not valid:
            continue
        top = max(valid, key=valid.get)
        signals.append(MonthlyMomentumSignal(date=date, weights={top: 1.0}, returns=valid))
    return signals
