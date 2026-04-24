import pandas as pd


def ibs(df: pd.DataFrame) -> pd.Series:
    """Internal Bar Strength: (Close - Low) / (High - Low), 0=저가, 1=고가"""
    hl = df["high"] - df["low"]
    return (df["close"] - df["low"]) / hl.replace(0, float("nan"))
