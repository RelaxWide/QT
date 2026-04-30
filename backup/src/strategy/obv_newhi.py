"""
OBV New High Dual Confirmation
가격 신고가 + OBV 신고가 동시 → 기관 매집 확인된 추세 돌파
  1. close > max(close[-hi_period:])   — 가격 신고가
  2. OBV  > max(OBV[-hi_period:])     — OBV 신고가 (매집 확인)
  3. close > MA200
  4. 진입: 다음날 시가
  5. 손절: ATR(14) × stop_mult
"""
import pandas as pd

from src.indicators.atr import atr as calc_atr
from src.backtest.swing_engine import SwingSignal


def _calc_obv(df: pd.DataFrame) -> pd.Series:
    delta = df["close"].diff()
    direction = pd.Series(0.0, index=df.index)
    direction[delta > 0] = 1.0
    direction[delta < 0] = -1.0
    return (direction * df["volume"]).cumsum()


def generate_obv_newhi_signals(
    symbol: str,
    df: pd.DataFrame,
    params: dict,
) -> list[SwingSignal]:
    hi_p    = params.get("hi_period",      20)
    ma200_p = params.get("ma200_period",  200)
    atr_p   = params.get("atr_period",    14)
    stop_m  = params.get("stop_atr_mult", 2.0)
    min_px  = params.get("min_price_usd",  10)
    min_vol = params.get("min_avg_volume", 500_000)

    warmup = max(hi_p, ma200_p) + 5
    if len(df) < warmup:
        return []

    close  = df["close"]
    volume = df["volume"]
    ma200  = close.rolling(ma200_p).mean()
    atr_s  = calc_atr(df, atr_p)
    avg_v  = volume.rolling(20).mean()
    obv    = _calc_obv(df)

    price_hi = close.rolling(hi_p).max().shift(1)
    obv_hi   = obv.rolling(hi_p).max().shift(1)

    all_dates = list(df.index)
    signals = []

    for i in range(warmup, len(all_dates) - 1):
        date = all_dates[i]
        c    = close.iloc[i]
        aval = atr_s.iloc[i]
        ov   = obv.iloc[i]

        if pd.isna(c) or pd.isna(aval) or pd.isna(ov):
            continue
        if c < min_px or avg_v.iloc[i] < min_vol:
            continue
        if c <= ma200.iloc[i] or pd.isna(ma200.iloc[i]):
            continue
        if c <= price_hi.iloc[i] or pd.isna(price_hi.iloc[i]):
            continue
        if ov <= obv_hi.iloc[i] or pd.isna(obv_hi.iloc[i]):
            continue

        entry_date = all_dates[i + 1]
        signals.append(SwingSignal(
            symbol=symbol,
            signal_date=date,
            entry_date=entry_date,
            stop_distance=stop_m * aval,
        ))

    return signals
