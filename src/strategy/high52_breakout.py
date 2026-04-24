"""
52-Week High + Volume Breakout 전략
출처: George & Hwang (2004), Journal of Financial Markets (2023) 재검증

진입 조건 (모두 충족):
  1. close == max(close[-252:])   — 52주 신고가 돌파
  2. volume > avg_volume(20) × volume_mult — 거래량 급증 확인
  3. close > MA200                — 장기 상승추세
  4. avg_volume >= min_avg_volume — 유동성 필터
  5. close >= min_price_usd
  6. 진입월 != 1월               — 1월 효과 역방향 회피

진입: 신호일 다음날 시가
손절: entry - stop_atr_mult × ATR(20)  (초기 손절)
트레일: 진입 후 최고 종가의 (1 - trail_pct) — 25% 트레일
MA200 이탈: close < MA200 → 다음날 시가 청산 (추세 훼손)
"""
from dataclasses import dataclass
import pandas as pd

from src.indicators.atr import atr as calc_atr


@dataclass
class High52Signal:
    symbol: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price_est: float   # 신호일 종가 (참고용)
    stop_distance: float     # stop_atr_mult × ATR
    atr_val: float
    ma200_val: float


def generate_high52_signals(
    symbol: str,
    df: pd.DataFrame,
    params: dict,
) -> list[High52Signal]:
    period_52w    = params.get("period_52week", 252)
    vol_mult      = params.get("volume_mult", 1.5)
    ma200_p       = params.get("ma200_period", 200)
    atr_p         = params.get("atr_period", 20)
    stop_mult     = params.get("stop_atr_mult", 2.0)
    min_price     = params.get("min_price_usd", 10)
    min_vol       = params.get("min_avg_volume", 500_000)
    no_january    = params.get("no_january_entry", True)

    warmup = max(period_52w, ma200_p) + 5
    if len(df) < warmup + 1:
        return []

    close  = df["close"]
    volume = df["volume"]

    high52   = close.rolling(period_52w).max().shift(1)  # 전일 기준 52주 최고
    ma200    = close.rolling(ma200_p).mean()
    atr_s    = calc_atr(df, atr_p)
    avg_vol  = volume.rolling(20).mean()

    signals: list[High52Signal] = []
    seen: set = set()

    for i in range(warmup, len(df) - 1):
        c  = close.iloc[i]
        v  = volume.iloc[i]
        h52 = high52.iloc[i]
        m200 = ma200.iloc[i]
        av   = avg_vol.iloc[i]
        at   = atr_s.iloc[i]

        if pd.isna(h52) or pd.isna(m200) or pd.isna(av) or pd.isna(at):
            continue
        if c < min_price:
            continue
        if av < min_vol:
            continue
        if c <= m200:
            continue
        if c <= h52:          # 52주 최고가 갱신이 아니면 스킵
            continue
        if v < av * vol_mult: # 거래량 급증 없으면 스킵
            continue

        entry_date = df.index[i + 1]
        if no_january and entry_date.month == 1:
            continue
        if entry_date in seen:
            continue

        signals.append(High52Signal(
            symbol=symbol,
            signal_date=df.index[i],
            entry_date=entry_date,
            entry_price_est=c,
            stop_distance=stop_mult * at,
            atr_val=at,
            ma200_val=m200,
        ))
        seen.add(entry_date)

    return signals
