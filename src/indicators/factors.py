"""
Phase 4 팩터 행렬 사전 계산

mom_rank  : DataFrame(date × symbol) — 63일 수익률 유니버스 내 백분위 (0~1, 높을수록 강함)
bbw_rank  : DataFrame(date × symbol) — BB폭 백분위 (0~1, 낮을수록 수축)
spy_mom   : Series(date) — SPY 63일 수익률
"""
import numpy as np
import pandas as pd


def build_factor_matrices(
    price_data: dict[str, pd.DataFrame],
    mom_period: int = 63,
    bb_period:  int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    close_mat = pd.DataFrame(
        {sym: df["close"] for sym, df in price_data.items()}
    ).sort_index()

    # ── Momentum ──────────────────────────────────────────────────────────
    mom_mat  = close_mat.pct_change(mom_period)
    mom_rank = mom_mat.rank(axis=1, pct=True, na_option="keep")

    # ── BB Width ──────────────────────────────────────────────────────────
    sma      = close_mat.rolling(bb_period).mean()
    std      = close_mat.rolling(bb_period).std()
    bbw_mat  = (4 * std / sma.replace(0, np.nan))
    bbw_rank = bbw_mat.rank(axis=1, pct=True, na_option="keep")

    # ── SPY momentum ──────────────────────────────────────────────────────
    spy_close = (
        close_mat["SPY"] if "SPY" in close_mat.columns
        else close_mat.iloc[:, 0]
    )
    spy_mom = spy_close.pct_change(mom_period)

    return mom_rank, bbw_rank, spy_mom
