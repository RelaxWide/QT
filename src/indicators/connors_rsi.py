"""
Connors RSI (CRSI) — StockCharts.com 표준

CRSI = (RSI(close, 3) + RSI(streak, 2) + PercentRank(1d_return, 100)) / 3

streak: 연속 상승/하락일 카운트
  close > prev → max(0, prev_streak) + 1
  close < prev → min(0, prev_streak) - 1
  close == prev → 0

PercentRank: 오늘 1일 수익률이 최근 100일 중 몇 % 보다 큰가 (0~100)
"""
import numpy as np
import pandas as pd


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    up    = delta.clip(lower=0)
    down  = -delta.clip(upper=0)
    roll_up   = up.ewm(alpha=1 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = roll_up / roll_down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _streak(close: pd.Series) -> pd.Series:
    diff = close.diff()
    streak = np.zeros(len(close), dtype=float)
    for i in range(1, len(close)):
        d = diff.iloc[i]
        if pd.isna(d) or d == 0:
            streak[i] = 0
        elif d > 0:
            streak[i] = max(0, streak[i - 1]) + 1
        else:
            streak[i] = min(0, streak[i - 1]) - 1
    return pd.Series(streak, index=close.index)


def _percent_rank(returns: pd.Series, period: int) -> pd.Series:
    """오늘 수익률보다 작은 과거 N일 수익률 비율 (0~100)."""
    def rank_pct(window):
        today = window[-1]
        past  = window[:-1]
        if np.isnan(today):
            return np.nan
        smaller = (past < today).sum()
        return smaller / len(past) * 100

    return returns.rolling(period + 1).apply(rank_pct, raw=True)


def connors_rsi(
    close: pd.Series,
    rsi_period:    int = 3,
    streak_period: int = 2,
    rank_period:   int = 100,
) -> pd.Series:
    rsi_close  = _rsi(close, rsi_period)
    rsi_streak = _rsi(_streak(close), streak_period)
    daily_ret  = close.pct_change()
    rank       = _percent_rank(daily_ret, rank_period)
    return (rsi_close + rsi_streak + rank) / 3
