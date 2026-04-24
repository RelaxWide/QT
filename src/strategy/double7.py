"""
Connors Double Seven Strategy (ETF only: SPY/QQQ/IWM)

Entry: close > MA200 AND close == min(close, 7 bars)
Exit:  close == max(close, 7 bars)
Stop:  catastrophic only (entry - stop_atr_mult × ATR)
"""
from dataclasses import dataclass

import pandas as pd

from src.indicators.atr import atr as calc_atr


@dataclass
class Double7Signal:
    symbol: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price_est: float
    stop_distance: float
    atr_val: float


def generate_double7_signals(symbol: str, df: pd.DataFrame, params: dict) -> list[Double7Signal]:
    ma200_p   = params.get("ma200_period", 200)
    low7_p    = params.get("low7_period", 7)
    atr_p     = params.get("atr_period", 20)
    stop_mult = params.get("stop_atr_mult", 3.0)

    if len(df) < ma200_p + low7_p + 2:
        return []

    close = df["close"]
    ma200 = close.rolling(ma200_p).mean()
    atr_s = calc_atr(df, atr_p)

    low7  = close.rolling(low7_p).min()

    signals: list[Double7Signal] = []

    for i in range(ma200_p + low7_p, len(df) - 1):
        c   = close.iloc[i]
        ma  = ma200.iloc[i]
        l7  = low7.iloc[i]

        if pd.isna(ma) or pd.isna(l7):
            continue

        # Entry: above MA200 AND today's close is the 7-day minimum
        if c <= ma:
            continue
        if c != l7:
            continue

        atr_val = atr_s.iloc[i]
        if pd.isna(atr_val) or atr_val <= 0:
            continue

        signals.append(Double7Signal(
            symbol=symbol,
            signal_date=df.index[i],
            entry_date=df.index[i + 1],
            entry_price_est=c,
            stop_distance=stop_mult * atr_val,
            atr_val=atr_val,
        ))

    return signals
