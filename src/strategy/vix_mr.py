"""
VIX Mean Reversion — 공포지수 급등 시 SPY 역매매

진입: VIX 종가 > entry_threshold (공포 급등)
청산: VIX 종가 < exit_threshold OR 보유 max_hold_days
손절: entry - stop_pct% (SPY 기준 고정 손절)
"""
from dataclasses import dataclass
import pandas as pd


@dataclass
class VIXSignal:
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price_est: float
    stop_pct: float           # 진입가 대비 손절 비율


def generate_vix_signals(
    spy_df: pd.DataFrame,
    vix_series: pd.Series,
    params: dict,
) -> list[VIXSignal]:
    entry_thr  = params.get("vix_entry_threshold", 25)
    stop_pct   = params.get("stop_pct", 0.05)   # 5% 손절
    spy_ma200p = params.get("spy_ma200_period", 200)

    close      = spy_df["close"]
    spy_ma200  = close.rolling(spy_ma200p).mean()

    signals: list[VIXSignal] = []
    in_signal = False

    for i in range(spy_ma200p, len(spy_df) - 1):
        date   = spy_df.index[i]
        c      = close.iloc[i]
        ma200  = spy_ma200.iloc[i]

        if pd.isna(ma200):
            continue

        # VIX 값
        if date not in vix_series.index:
            continue
        vix_val = vix_series.loc[date]
        if pd.isna(vix_val):
            continue

        # 이미 시그널 발생 중이면 skip (엔진이 처리)
        if in_signal:
            if vix_val < entry_thr:
                in_signal = False
            continue

        # 진입 조건: VIX 급등 (레짐 무관 — 공포는 어디서나 역매매 기회)
        if vix_val >= entry_thr:
            entry_date = spy_df.index[i + 1]
            signals.append(VIXSignal(
                signal_date=date,
                entry_date=entry_date,
                entry_price_est=c,
                stop_pct=stop_pct,
            ))
            in_signal = True

    return signals
