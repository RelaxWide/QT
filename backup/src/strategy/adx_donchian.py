"""
ADX-Filtered Donchian Breakout
Phase1 Donchian breakout + ADX(14) > adx_min 강도 필터 추가
  1. close > Donchian(don_period) 고점 (신고가 돌파)
  2. ADX(14) > adx_min (추세 강도)
  3. close > MA200 (레짐)
  4. 진입: 다음날 시가
  5. 손절: entry - stop_atr_mult × ATR
"""
import pandas as pd
import numpy as np

from src.indicators.atr import atr as calc_atr
from src.backtest.swing_engine import SwingSignal


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hi, lo, cl = df["high"], df["low"], df["close"]
    up   = hi.diff()
    down = -lo.diff()
    plus_dm  = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = pd.concat([hi - lo, (hi - cl.shift(1)).abs(), (lo - cl.shift(1)).abs()], axis=1).max(axis=1)
    atr14 = tr.ewm(alpha=1/period, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr14
    mdi = 100 * minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr14
    dx = (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan) * 100
    return dx.ewm(alpha=1/period, adjust=False).mean()


def generate_adx_donchian_signals(
    symbol: str,
    df: pd.DataFrame,
    params: dict,
) -> list[SwingSignal]:
    don_p    = params.get("donchian_period",  20)
    adx_min  = params.get("adx_min",          25)
    adx_p    = params.get("adx_period",       14)
    ma200_p  = params.get("ma200_period",    200)
    atr_p    = params.get("atr_period",       20)
    stop_m   = params.get("stop_atr_mult",   2.0)
    min_px   = params.get("min_price_usd",    10)
    min_vol  = params.get("min_avg_volume", 500_000)

    warmup = max(don_p, ma200_p, adx_p * 3) + 5
    if len(df) < warmup:
        return []

    close  = df["close"]
    volume = df["volume"]
    ma200  = close.rolling(ma200_p).mean()
    don_hi = close.rolling(don_p).max().shift(1)
    atr_s  = calc_atr(df, atr_p)
    adx_s  = _adx(df, adx_p)
    avg_v  = volume.rolling(20).mean()

    all_dates = list(df.index)
    signals = []

    for i in range(warmup, len(all_dates) - 1):
        date = all_dates[i]
        c    = close.iloc[i]
        aval = atr_s.iloc[i]
        adxv = adx_s.iloc[i]

        if pd.isna(c) or pd.isna(aval) or pd.isna(adxv):
            continue
        if c < min_px or avg_v.iloc[i] < min_vol:
            continue
        if c <= ma200.iloc[i] or pd.isna(ma200.iloc[i]):
            continue
        if c <= don_hi.iloc[i] or pd.isna(don_hi.iloc[i]):
            continue
        if adxv < adx_min:
            continue

        entry_date = all_dates[i + 1]
        signals.append(SwingSignal(
            symbol=symbol,
            signal_date=date,
            entry_date=entry_date,
            stop_distance=stop_m * aval,
        ))

    return signals
