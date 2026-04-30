"""
Bollinger Squeeze + Volume Breakout (John Carter / TTM Squeeze 변형)
  1. BB(20,2) width = (upper - lower) / mid — N일 최저 (squeeze 상태)
  2. close > upper BB + 거래량 > avg × vol_mult → 상방 돌파
  3. close > MA200
  4. 손절: lower BB 또는 ATR × stop_mult
"""
import pandas as pd

from src.indicators.atr import atr as calc_atr
from src.backtest.swing_engine import SwingSignal


def generate_bb_squeeze_signals(
    symbol: str,
    df: pd.DataFrame,
    params: dict,
) -> list[SwingSignal]:
    bb_p     = params.get("bb_period",       20)
    bb_std   = params.get("bb_std",         2.0)
    sq_p     = params.get("squeeze_period",  60)  # BB width 최저 관찰 기간
    vol_m    = params.get("volume_mult",    1.5)
    ma200_p  = params.get("ma200_period",  200)
    atr_p    = params.get("atr_period",     20)
    stop_m   = params.get("stop_atr_mult", 2.0)
    min_px   = params.get("min_price_usd",  10)
    min_vol  = params.get("min_avg_volume", 500_000)

    warmup = max(bb_p, ma200_p, sq_p) + 5
    if len(df) < warmup:
        return []

    close  = df["close"]
    volume = df["volume"]
    ma200  = close.rolling(ma200_p).mean()
    atr_s  = calc_atr(df, atr_p)
    avg_v  = volume.rolling(20).mean()

    mid   = close.rolling(bb_p).mean()
    std_s = close.rolling(bb_p).std()
    upper = mid + bb_std * std_s
    lower = mid - bb_std * std_s
    width = (upper - lower) / mid

    # 현재 width가 최근 sq_p 기간 최저인지
    width_min = width.rolling(sq_p).min().shift(1)

    all_dates = list(df.index)
    signals = []
    in_squeeze = False  # 이전 봉 squeeze 상태였는지

    for i in range(warmup, len(all_dates) - 1):
        date = all_dates[i]
        c    = close.iloc[i]
        v    = volume.iloc[i]
        aval = atr_s.iloc[i]
        w    = width.iloc[i]
        wmin = width_min.iloc[i]
        up_b = upper.iloc[i]
        lo_b = lower.iloc[i]

        if any(pd.isna(x) for x in [c, aval, w, wmin, up_b, lo_b]):
            continue
        if c < min_px or avg_v.iloc[i] < min_vol:
            continue
        if c <= ma200.iloc[i] or pd.isna(ma200.iloc[i]):
            continue

        # 직전 봉이 squeeze (width == min), 지금 상방 돌파
        prev_w = width.iloc[i - 1] if i > 0 else w
        was_squeezed = (prev_w <= wmin * 1.02)  # 2% 허용
        if not was_squeezed:
            continue
        if c <= up_b:
            continue
        if v < avg_v.iloc[i] * vol_m:
            continue

        stop_dist = max(c - lo_b, aval * stop_m)
        entry_date = all_dates[i + 1]
        signals.append(SwingSignal(
            symbol=symbol,
            signal_date=date,
            entry_date=entry_date,
            stop_distance=stop_dist,
        ))

    return signals
