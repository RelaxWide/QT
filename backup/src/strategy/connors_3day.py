"""
Larry Connors 3-Day High/Low Method
- 트렌드: SPY > MA200
- 진입: 3일 연속 종가 lower close (또는 3일 연속 lower high)
- 청산: 종가 > 5일 SMA
"""
import pandas as pd


def generate_signals(df: pd.DataFrame, ma_long: int = 200, ma_exit: int = 5) -> tuple[pd.Series, pd.Series]:
    close = df["close"]
    ma200 = close.rolling(ma_long).mean()
    ma5   = close.rolling(ma_exit).mean()

    diff = close.diff()
    lower3 = (diff < 0) & (diff.shift(1) < 0) & (diff.shift(2) < 0)

    entry = (close > ma200) & lower3
    exit_sig = close > ma5
    return entry.fillna(False), exit_sig.fillna(False)
