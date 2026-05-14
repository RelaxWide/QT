"""
Antonacci Global Equities Momentum (GEM).

Monthly rules:
- Compare 12 month total return of SPY and EFA.
- Hold the stronger equity ETF if its 12 month return is positive.
- Otherwise hold the bond proxy.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class GEMSignal:
    date: pd.Timestamp
    weights: dict[str, float]
    spy_ret: float
    efa_ret: float


def generate_gem_signals(
    price_data: dict[str, pd.DataFrame],
    lookback_months: int = 12,
    bond_proxy: str = "AGG",
) -> list[GEMSignal]:
    required = ["SPY", "EFA", bond_proxy]
    monthly = {
        sym: price_data[sym]["close"].resample("ME").last()
        for sym in required
        if sym in price_data and "close" in price_data[sym]
    }
    if "SPY" not in monthly or "EFA" not in monthly or bond_proxy not in monthly:
        return []

    close = pd.DataFrame(monthly).dropna()
    mom = close.pct_change(lookback_months)

    signals: list[GEMSignal] = []
    for date, row in mom.iloc[lookback_months:].iterrows():
        spy_ret = row["SPY"]
        efa_ret = row["EFA"]
        if pd.isna(spy_ret) or pd.isna(efa_ret):
            continue

        winner = "SPY" if spy_ret >= efa_ret else "EFA"
        winner_ret = spy_ret if winner == "SPY" else efa_ret
        hold = winner if winner_ret > 0 else bond_proxy
        signals.append(
            GEMSignal(
                date=date,
                weights={hold: 1.0},
                spy_ret=float(spy_ret),
                efa_ret=float(efa_ret),
            )
        )

    return signals
