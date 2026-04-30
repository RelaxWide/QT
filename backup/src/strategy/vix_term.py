"""
VIX Term Structure SPY Timing
^VIX9D / ^VIX 비율로 콘탱고/백워데이션 판단:
  - ratio < 1 (정상 콘탱고) → SPY 보유
  - ratio > 1 (백워데이션 — 단기 위험) → 현금/IEF
EMA로 노이즈 제거.
"""
import pandas as pd


def vix_term_signal(vix9d_close: pd.Series, vix_close: pd.Series, ema_period: int = 5) -> pd.Series:
    """True = 시장 진입(콘탱고), False = 회피(백워데이션)"""
    df = pd.concat({"v9": vix9d_close, "v": vix_close}, axis=1).dropna()
    ratio = df["v9"] / df["v"]
    smooth = ratio.ewm(span=ema_period, adjust=False).mean()
    return (smooth < 1.0)
