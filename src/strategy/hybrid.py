"""
Phase 3: Phase 1 (breakout-pullback) x Ichimoku cloud filter.

Entry = all Phase 1 signal conditions plus:
  F1. signal close > senkou_a > senkou_b
  F2. cloud thickness >= cloud_thickness_min_pct
  F3. tenkan > kijun
  F4. optional chikou: close > close[t-26]

Exit is handled by the common Phase 1 backtest/paper engine.
"""
import pandas as pd

from src.indicators.ichimoku import ichimoku
from src.strategy.breakout_pullback import generate_signals, Signal


def generate_hybrid_signals(
    symbol: str,
    df: pd.DataFrame,
    p1_params: dict,
    p2_params: dict,
) -> list[Signal]:
    p1_signals = generate_signals(symbol, df, p1_params)
    if not p1_signals:
        return []

    tenkan_p = p2_params["tenkan_period"]
    kijun_p = p2_params["kijun_period"]
    sb_p = p2_params["senkou_b_period"]
    shift = p2_params["chikou_offset"]
    thick_pct = p2_params.get("cloud_filter_thickness_min_pct", 2.0) / 100
    use_chikou = p2_params.get("cloud_filter_use_chikou", False)

    ich = ichimoku(df, tenkan_p, kijun_p, sb_p, shift)
    chikou_cond = df["close"] > df["close"].shift(shift)

    hybrid: list[Signal] = []
    for sig in p1_signals:
        entry_idx = df.index.get_loc(sig.entry_date)
        sig_idx = entry_idx - 1
        if sig_idx < 0:
            continue

        sa = ich["senkou_a"].iloc[sig_idx]
        sb = ich["senkou_b"].iloc[sig_idx]
        tenkan = ich["tenkan"].iloc[sig_idx]
        kijun = ich["kijun"].iloc[sig_idx]
        close = df["close"].iloc[sig_idx]

        if any(pd.isna(v) for v in [sa, sb, tenkan, kijun]):
            continue

        above_cloud = close > sa > sb
        thick_ok = (sa - sb) / close >= thick_pct
        trend_ok = tenkan > kijun

        if use_chikou and not chikou_cond.iloc[sig_idx]:
            continue

        if above_cloud and thick_ok and trend_ok:
            hybrid.append(sig)

    return hybrid
