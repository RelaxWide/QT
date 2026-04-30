"""
Darvas Box Swing Strategy
  1. 박스 형성: 최근 box_period 내 고점 후 N일 이상 하향 돌파 없으면 박스 상단 확정
  2. 박스 하단: 해당 기간 저가
  3. 진입: close > 박스 상단 + 거래량 > avg × vol_mult
  4. 손절: 박스 하단 - ATR × 0.5
"""
import pandas as pd

from src.indicators.atr import atr as calc_atr
from src.backtest.swing_engine import SwingSignal


def _find_box(highs: pd.Series, lows: pd.Series, idx: int, box_period: int, confirm_bars: int) -> tuple[float, float] | None:
    """idx 기준으로 box_period 내 마지막 박스 (top, bottom) 반환"""
    start = max(0, idx - box_period)
    window_hi = highs.iloc[start:idx]
    window_lo = lows.iloc[start:idx]
    if len(window_hi) < confirm_bars + 2:
        return None
    box_top    = window_hi.max()
    box_bottom = window_lo.min()
    # 박스 상단이 최소 confirm_bars 이상 유지됐는지 확인
    top_idx = window_hi.values.argmax()
    tail = window_hi.iloc[top_idx + 1:]
    if len(tail) < confirm_bars:
        return None
    if (tail > box_top * 0.995).any():
        return None
    return box_top, box_bottom


def generate_darvas_signals(
    symbol: str,
    df: pd.DataFrame,
    params: dict,
) -> list[SwingSignal]:
    box_p    = params.get("box_period",    20)
    conf_b   = params.get("confirm_bars",   3)
    vol_m    = params.get("volume_mult",   1.5)
    atr_p    = params.get("atr_period",    20)
    min_px   = params.get("min_price_usd", 10)
    min_vol  = params.get("min_avg_volume", 500_000)
    ma200_p  = params.get("ma200_period",  200)

    warmup = max(box_p, ma200_p) + 5
    if len(df) < warmup:
        return []

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]
    ma200  = close.rolling(ma200_p).mean()
    atr_s  = calc_atr(df, atr_p)
    avg_v  = volume.rolling(20).mean()

    all_dates = list(df.index)
    signals = []

    for i in range(warmup, len(all_dates) - 1):
        date = all_dates[i]
        c  = close.iloc[i]
        v  = volume.iloc[i]
        aval = atr_s.iloc[i]

        if pd.isna(c) or pd.isna(aval):
            continue
        if c < min_px or avg_v.iloc[i] < min_vol:
            continue
        if c <= ma200.iloc[i] or pd.isna(ma200.iloc[i]):
            continue

        box = _find_box(high, low, i, box_p, conf_b)
        if box is None:
            continue
        box_top, box_bottom = box

        # 돌파 확인
        if c <= box_top:
            continue
        if v < avg_v.iloc[i] * vol_m:
            continue

        stop_dist = max(c - box_bottom, aval * 1.0)
        entry_date = all_dates[i + 1]
        signals.append(SwingSignal(
            symbol=symbol,
            signal_date=date,
            entry_date=entry_date,
            stop_distance=stop_dist,
        ))

    return signals
