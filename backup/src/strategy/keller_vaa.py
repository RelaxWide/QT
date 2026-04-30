"""
Keller VAA-G4 — Vigilant Asset Allocation
모멘텀 점수 = 12·1M + 4·3M + 2·6M + 1·12M

규칙:
  - canary 자산 모두 양수 → top-1 offensive
  - canary 중 하나라도 음수 → top-1 defensive
"""
import pandas as pd


def vaa_score(close_monthly: pd.Series) -> pd.Series:
    """13612W 점수"""
    r1  = close_monthly.pct_change(1)
    r3  = close_monthly.pct_change(3)
    r6  = close_monthly.pct_change(6)
    r12 = close_monthly.pct_change(12)
    return 12 * r1 + 4 * r3 + 2 * r6 + r12


def select_vaa_asset(scores: dict[str, pd.Series], offensive: list[str], defensive: list[str],
                     canary: list[str], date: pd.Timestamp) -> str | None:
    """canary 검사 → offensive top-1 또는 defensive top-1"""
    canary_ok = True
    for c in canary:
        if c in scores and date in scores[c].index:
            v = scores[c].loc[date]
            if pd.isna(v) or v <= 0:
                canary_ok = False
                break

    pool = offensive if canary_ok else defensive
    best_sym, best = None, -1e9
    for sym in pool:
        if sym in scores and date in scores[sym].index:
            v = scores[sym].loc[date]
            if pd.notna(v) and v > best:
                best, best_sym = v, sym
    return best_sym
