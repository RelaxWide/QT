"""
Z-score Mean Reversion (reddit/algotrading)
- 진입: 20일 z-score ≤ -2 AND RSI(14) < 30
- 청산: z-score ≥ 0 OR 보유 12봉 OR 3×ATR 손절
"""
import numpy as np
import pandas as pd


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    dn = -delta.clip(upper=0)
    au = up.ewm(alpha=1/period, adjust=False).mean()
    ad = dn.ewm(alpha=1/period, adjust=False).mean()
    rs = au / ad.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calc_zscore(close: pd.Series, period: int = 20) -> pd.Series:
    ma = close.rolling(period).mean()
    sd = close.rolling(period).std()
    return (close - ma) / sd
