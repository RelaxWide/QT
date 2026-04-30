"""
High Win-Rate 전략: 이동평균 눌림목 반등

진입 조건 (모두 충족):
  1. 추세: close > MA50 > MA200
  2. 눌림: 최근 pb_max_bars 안에 저가가 MA20 이하 터치
  3. RSI: rsi_low <= RSI(14) <= rsi_high  (건강한 조정, 과매도 아님)
  4. 눌림 기간 평균 거래량 < 20일 평균 (distribution 없음)
  5. 반등봉: close > open  AND  close >= MA20  AND  거래량 >= avg_vol * vol_mult

손절: 눌림 저점 (swing low) 아래
목표: entry + target_r × R  (50% 부분 청산)
트레일: MA50 하향 이탈 시 잔여 청산
"""
from dataclasses import dataclass
import pandas as pd
import numpy as np

from src.indicators.atr import atr as calc_atr
from src.indicators.rsi import rsi as calc_rsi
from src.strategy.breakout_pullback import Signal


def generate_ma_bounce_signals(symbol: str, df: pd.DataFrame, params: dict) -> list[Signal]:
    min_price    = params.get("min_price_usd", 10)
    min_vol      = params.get("min_avg_volume", 500_000)
    ma20_p       = params.get("ma20_period", 20)
    ma50_p       = params.get("ma50_period", 50)
    ma200_p      = params.get("ma200_period", 200)
    rsi_p        = params.get("rsi_period", 14)
    rsi_low      = params.get("rsi_low", 35)
    rsi_high     = params.get("rsi_high", 58)
    pb_max_bars  = params.get("pullback_max_bars", 8)
    pb_tol       = params.get("ma20_touch_tolerance", 0.01)
    vol_mult     = params.get("bounce_volume_mult", 1.0)
    pb_vol_ratio = params.get("pullback_volume_ratio", 1.0)  # 눌림 거래량 < avg × ratio
    target_r     = params.get("target_r_multiple", 1.5)
    trail_p      = params.get("trail_ma_period", 50)

    if len(df) < ma200_p + 10:
        return []

    close   = df["close"]
    high    = df["high"]
    low     = df["low"]
    volume  = df["volume"]

    ma20  = close.rolling(ma20_p).mean()
    ma50  = close.rolling(ma50_p).mean()
    ma200 = close.rolling(ma200_p).mean()
    rsi_s = calc_rsi(close, rsi_p)
    atr_s = calc_atr(df, 14)
    avg_vol = volume.rolling(20).mean()

    # 상승 추세: close > MA50 > MA200
    uptrend = (close > ma50) & (ma50 > ma200)

    # 반등봉 조건 (벡터)
    bounce_candle = (
        (close > df["open"]) &           # 양봉
        (close >= ma20 * (1 - pb_tol)) & # MA20 이상 종가
        (rsi_s >= rsi_low) &
        (rsi_s <= rsi_high) &
        (volume >= avg_vol * vol_mult) &  # 거래량 확인
        uptrend &
        (close >= min_price) &
        (avg_vol >= min_vol)
    )

    signals: list[Signal] = []
    seen: set = set()

    for sig_date in df.index[bounce_candle]:
        loc = df.index.get_loc(sig_date)
        if loc < pb_max_bars + ma200_p:
            continue

        # 최근 pb_max_bars 안에 MA20 눌림 확인
        window_low  = low.iloc[loc - pb_max_bars: loc + 1]
        window_vol  = volume.iloc[loc - pb_max_bars: loc]
        ma20_window = ma20.iloc[loc - pb_max_bars: loc + 1]
        touched = (window_low <= ma20_window * (1 + pb_tol)).any()
        if not touched:
            continue

        # 눌림 기간 평균 거래량 < 전체 평균 (매도세 약함)
        avg_v = avg_vol.iloc[loc]
        if not pd.isna(avg_v) and window_vol.mean() >= avg_v * pb_vol_ratio:
            continue

        entry_loc = loc + 1
        if entry_loc >= len(df):
            continue
        entry_date = df.index[entry_loc]
        if entry_date in seen:
            continue

        entry_price = df["open"].iloc[entry_loc]
        if entry_price < min_price:
            continue

        # 손절: 눌림 구간 최저가 아래
        stop_price = window_low.min()
        atr_val    = atr_s.iloc[loc]
        if not pd.isna(atr_val):
            stop_price = min(stop_price, entry_price - atr_val)

        r = entry_price - stop_price
        if r <= 0 or r / entry_price > 0.12:
            continue

        signals.append(Signal(
            symbol=symbol,
            entry_date=entry_date,
            entry_price=entry_price,
            stop=stop_price,
            r=r,
            targets=[entry_price + target_r * r],
            partial_weights=[0.5],
            trail_period=trail_p,
        ))
        seen.add(entry_date)

    return signals
