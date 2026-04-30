"""
225-Day MA QQQ — financialwisdomtv 클레임
종가 > 225 SMA → 보유, 그 외 → 현금
"""
import pandas as pd


def generate_signal(close: pd.Series, ma_period: int = 225) -> pd.Series:
    """True = 보유, False = 현금"""
    ma = close.rolling(ma_period).mean()
    return (close > ma).fillna(False)
