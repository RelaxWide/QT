"""
Phase 4: Phase 3 Hybrid × Factor Stacking

추가 필터 (config에서 각각 활성화/비활성화):
  F1. 모멘텀: 63일 수익률 유니버스 내 상위 N% (기본 30%)
  F2. BB폭 수축: BB폭 유니버스 내 하위 N% (기본 20%, 에너지 응축)
  F3. SPY 상대강도: 개별 종목 63일 수익률 > SPY 63일 수익률

min_factors_required: 3개 중 최소 K개 통과 필요 (기본 2)
"""
import pandas as pd

from src.strategy.hybrid import generate_hybrid_signals
from src.strategy.breakout_pullback import Signal


def generate_factor_signals(
    symbol:    str,
    df:        pd.DataFrame,
    p1_params: dict,
    p2_params: dict,
    f_cfg:     dict,
    mom_rank:  pd.DataFrame,
    bbw_rank:  pd.DataFrame,
    spy_mom:   pd.Series,
) -> list[Signal]:
    phase3_sigs = generate_hybrid_signals(symbol, df, p1_params, p2_params)
    if not phase3_sigs:
        return []

    mom_thresh     = 1 - f_cfg["momentum_top_pct"] / 100   # 상위 30% → rank > 0.70
    bbw_thresh     = f_cfg["bbwidth_bottom_pct"]   / 100   # 하위 20% → rank < 0.20
    use_mom        = f_cfg.get("use_momentum",   True)
    use_bbw        = f_cfg.get("use_bbwidth",    True)
    use_rs         = f_cfg.get("use_spy_rs",     True)
    min_pass       = f_cfg.get("min_factors_required", 2)

    mom_period = f_cfg.get("momentum_period", 63)
    stock_mom  = df["close"].pct_change(mom_period)

    out: list[Signal] = []
    for sig in phase3_sigs:
        entry_idx = df.index.get_loc(sig.entry_date)
        sig_idx   = entry_idx - 1
        if sig_idx < 0:
            continue
        sig_date = df.index[sig_idx]

        score = 0

        # F1: Momentum
        if use_mom:
            try:
                r = mom_rank.at[sig_date, symbol]
                if not pd.isna(r) and r >= mom_thresh:
                    score += 1
            except KeyError:
                pass

        # F2: BB폭 수축
        if use_bbw:
            try:
                r = bbw_rank.at[sig_date, symbol]
                if not pd.isna(r) and r <= bbw_thresh:
                    score += 1
            except KeyError:
                pass

        # F3: SPY 상대강도
        if use_rs:
            try:
                sm = stock_mom.at[sig_date]
                sp = spy_mom.at[sig_date]
                if not pd.isna(sm) and not pd.isna(sp) and sm > sp:
                    score += 1
            except KeyError:
                pass

        total_active = sum([use_mom, use_bbw, use_rs])
        if score >= min(min_pass, total_active):
            out.append(sig)

    return out
