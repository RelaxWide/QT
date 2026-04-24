"""
Alvarez Mean Reversion 전략 (Cesar Alvarez, AlvarezQuantTrading.com)

진입 조건 (모두 충족):
  1. Close > MA(ma_fast_period)          — 단기 추세 위에 있는 종목
  2. Close < MA(ma_slow_period)          — (옵션) 장기 MA 아래도 허용 가능
  3. 연속 N일 lower lows (lower_lows_n)  — 눌림목 확인
  4. Close > MA(trend_ma_period)         — 상승추세주 필터 (MA100)
  5. avg_volume >= min_avg_volume        — 유동성
  6. close >= min_price_usd

진입: 다음날 지정가 = prev_close - limit_atr_mult × ATR(atr_period)
      (당일 저가가 지정가 이하 → 체결, 아니면 스킵)

청산: close > prev_close (전일 대비 상승 종가) → 당일 종가로 청산
손절: entry_price - stop_atr_mult × ATR(atr_period)  (재앙 방지용)
시간손절: max_hold_days 봉 경과 시 시가
"""
from dataclasses import dataclass
import pandas as pd

from src.indicators.atr import atr as calc_atr
from src.indicators.rsi import rsi as calc_rsi


@dataclass
class AlvarezSignal:
    symbol: str
    signal_date: pd.Timestamp   # T (조건 충족일)
    entry_date: pd.Timestamp    # T+1 (지정가 체결 시도일)
    limit_price: float          # T+1 지정가
    stop_distance: float        # stop_atr_mult × ATR
    atr_val: float


def generate_alvarez_signals(
    symbol: str,
    df: pd.DataFrame,
    params: dict,
) -> list[AlvarezSignal]:
    trend_ma_p    = params.get("trend_ma_period", 100)
    lower_lows_n  = params.get("lower_lows_n", 3)
    atr_p         = params.get("atr_period", 10)
    rsi_p         = params.get("rsi_period", 2)
    rsi_entry_max = params.get("rsi_entry_max", 20)   # RSI(2) < 이 값이어야 진입
    limit_mult    = params.get("limit_atr_mult", 0.5)
    stop_mult     = params.get("stop_atr_mult", 2.5)
    min_price     = params.get("min_price_usd", 10)
    min_vol       = params.get("min_avg_volume", 500_000)

    warmup = trend_ma_p + lower_lows_n + 5
    if len(df) < warmup + 1:
        return []

    close  = df["close"]
    low    = df["low"]
    volume = df["volume"]

    trend_ma = close.rolling(trend_ma_p).mean()
    atr_s    = calc_atr(df, atr_p)
    rsi_s    = calc_rsi(close, rsi_p)
    avg_vol  = volume.rolling(20).mean()

    signals: list[AlvarezSignal] = []
    seen: set = set()

    for i in range(warmup, len(df) - 1):
        c  = close.iloc[i]
        av = avg_vol.iloc[i]
        at = atr_s.iloc[i]
        tm = trend_ma.iloc[i]
        rv = rsi_s.iloc[i]

        if pd.isna(at) or pd.isna(tm) or pd.isna(av) or pd.isna(rv):
            continue
        if c < min_price:
            continue
        if av < min_vol:
            continue
        if c <= tm:
            continue
        if rv >= rsi_entry_max:
            continue

        # 연속 N일 lower lows (당일 포함)
        ok = True
        for j in range(lower_lows_n - 1):
            if low.iloc[i - j] >= low.iloc[i - j - 1]:
                ok = False
                break
        if not ok:
            continue

        entry_date = df.index[i + 1]
        if entry_date in seen:
            continue

        limit_px   = c - limit_mult * at
        stop_dist  = stop_mult * at

        signals.append(AlvarezSignal(
            symbol=symbol,
            signal_date=df.index[i],
            entry_date=entry_date,
            limit_price=limit_px,
            stop_distance=stop_dist,
            atr_val=at,
        ))
        seen.add(entry_date)

    return signals
