"""
Meb Faber GTAA-5 style tactical asset allocation.

Monthly rules:
- Five assets receive equal target slots.
- If an asset is above its 10-month SMA, hold that asset.
- Otherwise move that slot to the cash proxy.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class GTAASignal:
    date: pd.Timestamp
    weights: dict[str, float]
    above_sma: dict[str, bool]


def generate_gtaa_signals(
    price_data: dict[str, pd.DataFrame],
    assets: list[str],
    cash_proxy: str = "BIL",
    sma_months: int = 10,
) -> list[GTAASignal]:
    monthly = {
        sym: price_data[sym]["close"].resample("ME").last()
        for sym in sorted(set(assets + [cash_proxy]))
        if sym in price_data and "close" in price_data[sym]
    }
    if not all(sym in monthly for sym in assets):
        return []

    close = pd.DataFrame({sym: monthly[sym] for sym in assets}).dropna()
    sma = close.rolling(sma_months).mean()
    signals: list[GTAASignal] = []
    slot = 1.0 / len(assets)

    for date, row in close.iloc[sma_months - 1 :].iterrows():
        weights: dict[str, float] = {}
        above: dict[str, bool] = {}
        for sym in assets:
            is_above = bool(row[sym] > sma.loc[date, sym])
            above[sym] = is_above
            target = sym if is_above else cash_proxy
            weights[target] = weights.get(target, 0.0) + slot
        signals.append(GTAASignal(date=date, weights=weights, above_sma=above))

    return signals
