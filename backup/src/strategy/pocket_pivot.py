"""
Pocket Pivot (O'Neill / Minervini)
진입 조건:
  1. close > 10MA (단기 추세)
  2. 50MA > 200MA (상승 구조)
  3. 당일 거래량 > 직전 10거래일 중 **하락일** 최대 거래량 (기관 매집 신호)
  4. close > min_price, avg_volume > min_avg_volume
손절: ATR(14) × stop_mult
"""
from dataclasses import dataclass

import pandas as pd

from src.indicators.atr import atr as calc_atr
from src.backtest.swing_engine import SwingSignal


def generate_pocket_pivot_signals(
    symbol: str,
    df: pd.DataFrame,
    params: dict,
) -> list[SwingSignal]:
    ma10_p   = params.get("ma10_period",    10)
    ma50_p   = params.get("ma50_period",    50)
    ma200_p  = params.get("ma200_period",  200)
    vol_win  = params.get("vol_lookback",   10)
    atr_p    = params.get("atr_period",     14)
    stop_m   = params.get("stop_atr_mult", 1.5)
    min_px   = params.get("min_price_usd",  10)
    min_vol  = params.get("min_avg_volume", 500_000)

    warmup = max(ma200_p, 252) + 5
    if len(df) < warmup:
        return []

    close  = df["close"]
    volume = df["volume"]
    ma10   = close.rolling(ma10_p).mean()
    ma50   = close.rolling(ma50_p).mean()
    ma200  = close.rolling(ma200_p).mean()
    atr_s  = calc_atr(df, atr_p)
    avg_v  = volume.rolling(20).mean()

    # 하락일 거래량 (close < prev close)
    is_down = close < close.shift(1)
    down_vol = volume.where(is_down, 0.0)

    signals = []
    all_dates = list(df.index)
    date_idx = {d: i for i, d in enumerate(all_dates)}

    for i in range(warmup, len(all_dates) - 1):
        date = all_dates[i]
        c = close.iloc[i]
        v = volume.iloc[i]
        aval = atr_s.iloc[i]
        avg = avg_v.iloc[i]

        if pd.isna(c) or pd.isna(aval) or pd.isna(avg):
            continue
        if c < min_px or avg < min_vol:
            continue
        if c <= ma10.iloc[i] or pd.isna(ma10.iloc[i]):
            continue
        if ma50.iloc[i] <= ma200.iloc[i]:
            continue

        # 직전 vol_win 하락일 최대 거래량
        max_down_vol = down_vol.iloc[max(0, i - vol_win):i].max()
        if pd.isna(max_down_vol) or max_down_vol <= 0:
            continue
        if v <= max_down_vol:
            continue

        entry_date = all_dates[i + 1]
        signals.append(SwingSignal(
            symbol=symbol,
            signal_date=date,
            entry_date=entry_date,
            stop_distance=stop_m * aval,
        ))

    return signals
