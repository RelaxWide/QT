import pandas as pd


def donchian_channel(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    # shift(1) → at bar t, we only see data through t-1 (no lookahead)
    upper = df["high"].rolling(period).max().shift(1)
    lower = df["low"].rolling(period).min().shift(1)
    return pd.DataFrame({"upper": upper, "lower": lower, "mid": (upper + lower) / 2}, index=df.index)
