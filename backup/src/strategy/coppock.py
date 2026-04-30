"""
Coppock Curve — Edwin Coppock (1962)
SPY 월간 데이터 기반 장기 모멘텀 지표.
Coppock = WMA(ROC14M + ROC11M, 10)
음→양 전환 시 매수, 양→음 전환 시 청산.
"""
import numpy as np
import pandas as pd


def _wma(s: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1)
    return s.rolling(period).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def coppock_curve(close_monthly: pd.Series, roc1: int = 14, roc2: int = 11, wma_p: int = 10) -> pd.Series:
    r1 = close_monthly.pct_change(roc1) * 100
    r2 = close_monthly.pct_change(roc2) * 100
    return _wma(r1 + r2, wma_p)


def generate_monthly_signals(close_daily: pd.Series, roc1: int = 14, roc2: int = 11, wma_p: int = 10) -> pd.Series:
    monthly = close_daily.resample("ME").last()
    cc = coppock_curve(monthly, roc1, roc2, wma_p)
    sig = (cc > 0).astype(int)
    flip = sig.diff().fillna(0)
    return flip
