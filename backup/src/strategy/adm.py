"""
Accelerating Dual Momentum (Engineered Portfolio, 2018)
SPY vs SCZ — 1+3+6M 가속 모멘텀 점수 비교, 둘 다 음수 → 채권.
"""
import pandas as pd


def momentum_score(close_monthly: pd.Series) -> pd.Series:
    """1M + 3M + 6M 합산 수익률"""
    r1 = close_monthly.pct_change(1)
    r3 = close_monthly.pct_change(3)
    r6 = close_monthly.pct_change(6)
    return r1 + r3 + r6


def select_asset(scores: dict[str, pd.Series], offensive: list[str], defensive: str, date: pd.Timestamp) -> str:
    """월말 일자에서 자산 선택"""
    best_sym = None
    best_score = -1e9
    for sym in offensive:
        s = scores[sym]
        if date not in s.index:
            continue
        v = s.loc[date]
        if pd.notna(v) and v > best_score:
            best_score = v
            best_sym = sym
    if best_sym is None or best_score <= 0:
        return defensive
    return best_sym
