"""
IBS (Internal Bar Strength) 평균회귀 전략

진입 조건 (모두 충족):
  1. IBS = (Close-Low)/(High-Low) < ibs_threshold  (장 막판 약세)
  2. 당일 종가 < 전일 종가                          (하락일 확인)
  3. 종가 > MA200                                   (상승추세주만)
  4. 당일 저가가 최근 N일 최저 저가                  (5일 저점 갱신)
  5. SPY > SPY_MA50                                 (레짐 필터 — 호출부에서 처리)

손절: 다음날 시가 - stop_atr_mult × ATR(14)
목표: 신호일 고가 (전일 고가 기준 반등 확인)
시간손절: max_hold_days 봉 경과 시 시가 청산
"""
from dataclasses import dataclass
import pandas as pd

from src.indicators.ibs import ibs as calc_ibs
from src.indicators.atr import atr as calc_atr


@dataclass
class IBSSignal:
    symbol: str
    entry_date: pd.Timestamp   # T+1 (시가 진입일)
    entry_price_est: float     # 신호일 종가 (참고용)
    stop_distance: float       # stop_atr_mult × ATR (진입가에서 차감 거리)
    target_price: float        # 신호일 고가
    signal_date: pd.Timestamp  # T


def generate_ibs_signals(symbol: str, df: pd.DataFrame, params: dict) -> list[IBSSignal]:
    ibs_thr    = params.get("ibs_threshold", 0.25)
    ma200_p    = params.get("ma200_period", 200)
    atr_p      = params.get("atr_period", 14)
    stop_mult  = params.get("stop_atr_mult", 0.75)          # 진입가 - N×ATR
    min_price  = params.get("min_price_usd", 10)
    min_vol    = params.get("min_avg_volume", 500_000)
    low_bars   = params.get("five_day_low_bars", 5)

    if len(df) < ma200_p + low_bars + 5:
        return []

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    ibs_s   = calc_ibs(df)
    ma200   = close.rolling(ma200_p).mean()
    atr_s   = calc_atr(df, atr_p)
    avg_vol = volume.rolling(20).mean()

    signals: list[IBSSignal] = []
    seen: set = set()

    for i in range(ma200_p + low_bars, len(df) - 1):
        if pd.isna(ibs_s.iloc[i]) or ibs_s.iloc[i] >= ibs_thr:
            continue
        if close.iloc[i] <= ma200.iloc[i]:
            continue
        if close.iloc[i] >= close.iloc[i - 1]:      # 반드시 하락일
            continue
        if avg_vol.iloc[i] < min_vol:
            continue
        if close.iloc[i] < min_price:
            continue

        # 최근 low_bars일 저가 중 오늘 저가가 최저 (5일 저점 갱신)
        window_low = low.iloc[i - low_bars + 1: i + 1]
        if low.iloc[i] > window_low.min():
            continue

        entry_date = df.index[i + 1]
        if entry_date in seen:
            continue

        atr_val = atr_s.iloc[i]
        if pd.isna(atr_val) or atr_val <= 0:
            continue

        signals.append(IBSSignal(
            symbol=symbol,
            entry_date=entry_date,
            entry_price_est=close.iloc[i],
            stop_distance=stop_mult * atr_val,  # 진입가에서 차감
            target_price=high.iloc[i],          # 신호일 고가 = 익일 기준 "전일 고가"
            signal_date=df.index[i],
        ))
        seen.add(entry_date)

    return signals
