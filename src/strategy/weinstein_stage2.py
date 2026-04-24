"""
Weinstein Stage 2 Breakout (주봉)

진입: 종가가 30주 MA를 처음 상향 돌파 + 거래량 확인
청산: 종가 < 30주 MA (추세 붕괴)
필터: 52주 고가 75% 이내 (고점 근처)
"""
from dataclasses import dataclass
import pandas as pd


@dataclass
class WeinsteinSignal:
    symbol: str
    signal_week: pd.Timestamp   # 주봉 신호일
    entry_date: pd.Timestamp    # 다음 거래일
    entry_price_est: float
    ma30_val: float
    volume_ratio: float = 1.0   # 돌파 주 거래량 / 10주 평균 (랭킹용)


def _resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample("W-WED").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna(subset=["close"])


def generate_weinstein_signals(
    symbol: str,
    df: pd.DataFrame,
    params: dict,
) -> list[WeinsteinSignal]:
    ma30_p     = params.get("ma30_period", 30)
    vol_mult   = params.get("volume_mult", 2.0)
    high52_pct = params.get("high52_within_pct", 0.20)
    min_price  = params.get("min_price_usd", 10)

    if len(df) < ma30_p * 5 + 10:
        return []

    wdf     = _resample_weekly(df)
    close   = wdf["close"]
    vol     = wdf["volume"]
    ma30    = close.rolling(ma30_p).mean()
    avg_vol = vol.rolling(10).mean()
    high52  = close.rolling(52).max()

    signals: list[WeinsteinSignal] = []

    for i in range(ma30_p + 1, len(wdf) - 1):
        c       = close.iloc[i]
        c_prev  = close.iloc[i - 1]
        ma      = ma30.iloc[i]
        ma_prev = ma30.iloc[i - 1]
        av      = avg_vol.iloc[i]
        h52     = high52.iloc[i]

        if pd.isna(ma) or pd.isna(av) or pd.isna(h52):
            continue
        if c < min_price:
            continue

        # Stage 2 돌파: 이번 주 종가 > MA30 AND 지난 주 종가 < MA30
        if not (c > ma and c_prev <= ma_prev):
            continue

        # 거래량 확인: 이번 주 거래량 > 10주 평균 × 배수 (강화: 1.5→2.0)
        week_vol = vol.iloc[i]
        if week_vol < av * vol_mult:
            continue

        # 52주 고가 20% 이내 (Stage 2 구간, 강화)
        if c < h52 * (1 - high52_pct):
            continue

        # 진입일: 다음 거래일
        signal_week = wdf.index[i]
        daily_after = df.index[df.index > signal_week]
        if len(daily_after) == 0:
            continue
        entry_date = daily_after[0]

        signals.append(WeinsteinSignal(
            symbol=symbol,
            signal_week=signal_week,
            entry_date=entry_date,
            entry_price_est=c,
            ma30_val=float(ma),
            volume_ratio=float(week_vol / av),
        ))

    return signals
