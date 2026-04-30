"""
Leveraged 200MA — UPRO/TQQQ + 신호 자산 200MA
SPY (signal) > 200MA → UPRO (leveraged) 보유
SPY < 200MA → 현금 또는 IEF
"""
import pandas as pd


def leveraged_signal(signal_close: pd.Series, ma_period: int = 200, buffer_pct: float = 0.0) -> pd.Series:
    ma = signal_close.rolling(ma_period).mean()
    if buffer_pct > 0:
        return (signal_close > ma * (1 + buffer_pct))
    return signal_close > ma
