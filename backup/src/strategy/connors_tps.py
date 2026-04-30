"""
Connors TPS (Time/Price Scale-In) — ETF Mean Reversion

진입 조건:
  1. 종가 > 200일 SMA (장기 상승)
  2. RSI(2) < 25 가 2일 연속
  → 자본의 10% 진입 (1차)

스케일인 (각 레이어당 +1):
  - 다음 거래일 종가가 직전 진입가보다 낮으면 추가 진입
  - 비중: 10% → 20% → 30% → 40% (누적 10/30/60/100%)

청산:
  - RSI(2) > 70 → 전 레이어 일괄 청산 (다음날 시가)

유니버스: 주요 ETF 10개
"""
import numpy as np
import pandas as pd


def calc_rsi(close: pd.Series, period: int = 2) -> pd.Series:
    delta = close.diff()
    up    = delta.clip(lower=0)
    down  = -delta.clip(upper=0)
    avg_up   = up.ewm(alpha=1 / period, adjust=False).mean()
    avg_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


# 누적 비중 (1차/2차/3차/4차)
SCALE_PCT = [0.10, 0.20, 0.30, 0.40]


DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "IWM", "EFA", "EEM",
    "GLD", "IYR", "XLF", "XLE", "XLK",
]
