"""
Phase 3: Phase 1 (돌파-풀백) × Ichimoku 구름 필터 교집합

진입 = Phase 1 신호 조건 전부 +
  F1. 신호봉 종가 > senkou_a > senkou_b  (상승 구름 위)
  F2. (senkou_a - senkou_b) / 종가 >= cloud_thickness_min_pct  (두꺼운 구름)
  F3. tenkan > kijun  (단기 추세 정렬)
  F4. (선택) chikou: 종가 > 종가[t-26]  (후행스팬 확인)

청산 = Phase 1과 동일 (3R 목표 50% + 돈치안 트레일 잔여 50%)
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
    kijun_p  = p2_params["kijun_period"]
    sb_p     = p2_params["senkou_b_period"]
    shift    = p2_params["chikou_offset"]
    thick_pct = p2_params.get("cloud_filter_thickness_min_pct", 2.0) / 100
    use_chikou = p2_params.get("cloud_filter_use_chikou", False)

    ich = ichimoku(df, tenkan_p, kijun_p, sb_p, shift)
    chikou_cond = df["close"] > df["close"].shift(shift)

    hybrid: list[Signal] = []
    for sig in p1_signals:
        entry_idx = df.index.get_loc(sig.entry_date)
        sig_idx   = entry_idx - 1
        if sig_idx < 0:
            continue

        sa     = ich["senkou_a"].iloc[sig_idx]
        sb     = ich["senkou_b"].iloc[sig_idx]
        tenkan = ich["tenkan"].iloc[sig_idx]
        kijun  = ich["kijun"].iloc[sig_idx]
        close  = df["close"].iloc[sig_idx]

        if any(pd.isna(v) for v in [sa, sb, tenkan, kijun]):
            continue

        above_cloud = (close > sa) and (sa > sb)
        thick_ok    = (sa - sb) / close >= thick_pct
        trend_ok    = tenkan > kijun

        if use_chikou and not chikou_cond.iloc[sig_idx]:
            continue

        if above_cloud and thick_ok and trend_ok:
            hybrid.append(sig)

    return hybrid
