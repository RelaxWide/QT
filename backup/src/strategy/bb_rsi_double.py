"""
Bollinger Bands + RSI Double (ChartArt v1.1)
- 진입: RSI(6) < 50 AND 종가가 lower BB(200,2) 하향 돌파
- 청산: RSI(6) > 50 AND 종가가 upper BB(200,2) 상향 돌파
"""
import numpy as np
import pandas as pd


def calc_rsi(close: pd.Series, period: int = 6) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    dn = -delta.clip(upper=0)
    au = up.ewm(alpha=1/period, adjust=False).mean()
    ad = dn.ewm(alpha=1/period, adjust=False).mean()
    rs = au / ad.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def generate_signals(df: pd.DataFrame, bb_period: int = 200, bb_std: float = 2.0,
                     rsi_period: int = 6, rsi_thresh: float = 50) -> tuple[pd.Series, pd.Series]:
    close = df["close"]
    ma = close.rolling(bb_period).mean()
    std = close.rolling(bb_period).std()
    upper = ma + bb_std * std
    lower = ma - bb_std * std
    rsi = calc_rsi(close, rsi_period)

    cross_below_lower = (close < lower) & (close.shift(1) >= lower.shift(1))
    cross_above_upper = (close > upper) & (close.shift(1) <= upper.shift(1))

    entry = cross_below_lower & (rsi < rsi_thresh)
    exit_ = cross_above_upper & (rsi > rsi_thresh)
    return entry.fillna(False), exit_.fillna(False)
