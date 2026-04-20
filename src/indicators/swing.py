import pandas as pd


def swing_low(series: pd.Series, lookback: int = 10) -> pd.Series:
    return series.rolling(lookback).min().shift(1)
