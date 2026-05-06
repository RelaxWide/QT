"""
Phase 4-v2: Anticipatory Cloud × SPY RS 팩터

anticipatory_cloud 신호 위에 SPY 상대강도 필터만 적용.
(BBW, 모멘텀 랭크는 구름 선접근 전략과 역방향 — 제거)
"""
import pandas as pd

from src.strategy.anticipatory_cloud import generate_anticipatory_signals
from src.strategy.breakout_pullback import Signal


def generate_anticipatory_factor_signals(
    symbol:   str,
    df:       pd.DataFrame,
    params:   dict,
    spy_mom:  pd.Series,
) -> list[Signal]:
    base_sigs = generate_anticipatory_signals(symbol, df, params)
    if not base_sigs:
        return []

    use_rs     = params.get("use_spy_rs", True)
    mom_period = params.get("momentum_period", 63)
    stock_mom  = df["close"].pct_change(mom_period)

    if not use_rs:
        return base_sigs

    out: list[Signal] = []
    for sig in base_sigs:
        entry_idx = df.index.get_loc(sig.entry_date)
        sig_idx   = entry_idx - 1
        if sig_idx < 0:
            out.append(sig)
            continue
        sig_date = df.index[sig_idx]

        try:
            sm = stock_mom.at[sig_date]
            sp = spy_mom.at[sig_date]
            if pd.isna(sm) or pd.isna(sp) or sm <= sp:
                continue
        except KeyError:
            pass

        out.append(sig)

    return out
