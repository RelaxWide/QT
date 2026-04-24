"""
Connors RSI(2) — SPY 단독 평균회귀

진입: SPY > MA200 AND RSI(2) < rsi_entry_max
청산: RSI(2) > rsi_exit_min  (당일 종가 확인 → 다음날 시가)
손절: 재앙 방지용 (entry - stop_atr_mult × ATR)
"""
from dataclasses import dataclass
import pandas as pd
import numpy as np

from src.indicators.atr import atr as calc_atr


@dataclass
class RSI2Signal:
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price_est: float
    stop_distance: float


def _calc_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def generate_rsi2_signals(df: pd.DataFrame, params: dict) -> list[RSI2Signal]:
    ma200_p   = params.get("ma200_period", 200)
    rsi_p     = params.get("rsi_period", 2)
    entry_max = params.get("rsi_entry_max", 10)
    atr_p     = params.get("atr_period", 20)
    stop_mult = params.get("stop_atr_mult", 3.0)

    if len(df) < ma200_p + 5:
        return []

    close = df["close"]
    ma200 = close.rolling(ma200_p).mean()
    rsi   = _calc_rsi(close, rsi_p)
    atr_s = calc_atr(df, atr_p)

    signals: list[RSI2Signal] = []

    for i in range(ma200_p, len(df) - 1):
        if pd.isna(rsi.iloc[i]) or pd.isna(ma200.iloc[i]):
            continue

        c  = close.iloc[i]
        ma = ma200.iloc[i]
        rv = rsi.iloc[i]

        if c <= ma:
            continue
        if rv >= entry_max:
            continue

        atr_val = atr_s.iloc[i]
        if pd.isna(atr_val) or atr_val <= 0:
            continue

        signals.append(RSI2Signal(
            signal_date=df.index[i],
            entry_date=df.index[i + 1],
            entry_price_est=c,
            stop_distance=stop_mult * atr_val,
        ))

    return signals
