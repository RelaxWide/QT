"""
글로벌 합성 분석 — KOSPI 2025-03 상승장 컷오프 재계산.

원본 analyze_global_synthesis.py 와 동일 로직 + CUTOFF (2025-02-28).
US Clenow + Clenow KR + KW Super Value 3자 가중 sweep.

사용:
    python analyze_global_synthesis_excl.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


CUTOFF = pd.Timestamp("2025-02-28")


def load_eq(path: str) -> pd.Series:
    return pd.read_csv(path, index_col="date", parse_dates=["date"])["equity"]


def metrics(eq: pd.Series, initial: float, label: str = "") -> dict:
    eq = eq.dropna()
    if eq.empty or eq.iloc[0] <= 0:
        return {}
    daily = eq.pct_change().dropna()
    n_years = len(eq) / 252
    cagr = (eq.iloc[-1] / initial) ** (1/n_years) - 1 if n_years > 0 else 0
    roll = eq.cummax()
    dd = (eq - roll) / roll
    mdd = float(dd.min())
    sharpe  = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0
    sortino = daily.mean() / daily[daily<0].std() * np.sqrt(252) if daily[daily<0].std() > 0 else 0
    calmar  = cagr / abs(mdd) if mdd != 0 else 0
    return {
        "label":            label,
        "cagr_pct":         round(cagr*100, 2),
        "mdd_pct":          round(mdd*100, 2),
        "sharpe":           round(sharpe, 3),
        "sortino":          round(sortino, 3),
        "calmar":           round(calmar, 3),
        "total_return_pct": round((eq.iloc[-1]/initial - 1)*100, 2),
    }


def synth_returns(returns_list: list[tuple[pd.Series, float]], initial: float = 100_000) -> pd.Series:
    """가중 returns 합성 → equity curve."""
    common = None
    for r, _ in returns_list:
        common = r.index if common is None else common.intersection(r.index)
    if common is None or len(common) == 0:
        return pd.Series(dtype=float)
    combined = pd.Series(0.0, index=common)
    for r, w in returns_list:
        combined = combined.add(r.loc[common] * w, fill_value=0)
    eq = initial * (1 + combined).cumprod()
    return eq


def run_sweep(label: str, us_ret, kr_clenow_ret, kr_sv_ret, common, initial):
    rows = []
    for w_us in np.arange(0, 1.01, 0.1):
        for w_kr_c in np.arange(0, 1.01 - w_us, 0.1):
            w_kr_sv = round(1 - w_us - w_kr_c, 4)
            if w_kr_sv < -1e-9:
                continue
            w_kr_sv = max(0, w_kr_sv)
            eq = synth_returns([
                (us_ret.loc[common], w_us),
                (kr_clenow_ret.loc[common], w_kr_c),
                (kr_sv_ret.loc[common], w_kr_sv),
            ], initial)
            m = metrics(eq, initial)
            if not m: continue
            m["w_us"] = round(float(w_us), 2)
            m["w_clenow_kr"] = round(float(w_kr_c), 2)
            m["w_kw_sv"] = round(float(w_kr_sv), 2)
            rows.append(m)
    df = pd.DataFrame(rows)
    print()
    print(f"=== {label} — Top 5 Sharpe ===")
    print(df.nlargest(5, "sharpe")[["w_us","w_clenow_kr","w_kw_sv","cagr_pct","mdd_pct","sharpe","calmar"]].to_string(index=False))
    print(f"=== {label} — Top 5 Calmar ===")
    print(df.nlargest(5, "calmar")[["w_us","w_clenow_kr","w_kw_sv","cagr_pct","mdd_pct","sharpe","calmar"]].to_string(index=False))
    return df


def main():
    us_clenow = load_eq("backtest_results/clenow_equity.csv")
    kr_clenow = load_eq("backtest_results/kr/clenow_equity.csv")
    kr_sv     = load_eq("backtest_results/kr/kw_super_value_equity.csv")

    us_ret        = us_clenow.pct_change().fillna(0)
    kr_clenow_ret = kr_clenow.pct_change().fillna(0)
    kr_sv_ret     = kr_sv.pct_change().fillna(0)

    initial = 100_000.0

    print("=" * 100)
    print(f"개별 전략 정규화 (initial={initial:,.0f})")
    print("=" * 100)
    for name, ret in [("US Clenow", us_ret), ("Clenow KR", kr_clenow_ret), ("KW Super Value", kr_sv_ret)]:
        eq = initial * (1 + ret).cumprod()
        eq_cut = initial * (1 + ret[ret.index <= CUTOFF]).cumprod()
        m_full = metrics(eq, initial)
        m_cut  = metrics(eq_cut, initial)
        print(f"  {name:18s} 전체: CAGR {m_full['cagr_pct']:>+6.2f}% Sharpe {m_full['sharpe']:.2f}    "
              f"컷오프({CUTOFF.date()}): CAGR {m_cut['cagr_pct']:>+6.2f}% Sharpe {m_cut['sharpe']:.2f}")

    # 컷오프별 sweep
    common_full = us_ret.index.intersection(kr_clenow_ret.index).intersection(kr_sv_ret.index)
    common_cut  = common_full[common_full <= CUTOFF]

    print()
    print("=" * 100)
    print("상관계수 매트릭스 (전체)")
    print("=" * 100)
    df_full = pd.DataFrame({"US_Clenow": us_ret.loc[common_full], "Clenow_KR": kr_clenow_ret.loc[common_full], "KW_SV": kr_sv_ret.loc[common_full]})
    print(df_full.corr().round(3))

    print()
    print("상관계수 매트릭스 (컷오프)")
    df_cut = pd.DataFrame({"US_Clenow": us_ret.loc[common_cut], "Clenow_KR": kr_clenow_ret.loc[common_cut], "KW_SV": kr_sv_ret.loc[common_cut]})
    print(df_cut.corr().round(3))

    print()
    df_full_sweep = run_sweep("[전체 기간 — 2026-05까지]", us_ret, kr_clenow_ret, kr_sv_ret, common_full, initial)
    df_cut_sweep  = run_sweep(f"[컷오프 — {CUTOFF.date()}까지, 상승장 제외]", us_ret, kr_clenow_ret, kr_sv_ret, common_cut, initial)

    # 비교 표
    print()
    print("=" * 100)
    print("주요 비율 — 전체 vs 컷오프 비교")
    print("=" * 100)
    candidates = [
        (1.0, 0.0, 0.0, "US 단독"),
        (0.0, 1.0, 0.0, "Clenow KR 단독"),
        (0.0, 0.0, 1.0, "KW SV 단독"),
        (0.5, 0.3, 0.2, "기존 최적 50/30/20"),
        (0.4, 0.5, 0.1, "기존 Calmar 40/50/10"),
        (0.5, 0.0, 0.5, "US 50 / KW SV 50"),
        (0.4, 0.0, 0.6, "US 40 / KW SV 60"),
        (0.3, 0.0, 0.7, "US 30 / KW SV 70"),
        (0.5, 0.1, 0.4, "US 50 / Clenow 10 / KW SV 40"),
    ]
    print(f"{'배분':30s} {'CAGR 전체':>10s} {'Sharpe 전체':>12s} {'CAGR 컷':>10s} {'Sharpe 컷':>11s} {'MDD 컷':>9s} {'Δ Sharpe':>10s}")
    print("-" * 105)
    for w_us, w_kc, w_kv, label in candidates:
        eq_f = synth_returns([(us_ret.loc[common_full], w_us),(kr_clenow_ret.loc[common_full], w_kc),(kr_sv_ret.loc[common_full], w_kv)], initial)
        eq_c = synth_returns([(us_ret.loc[common_cut],  w_us),(kr_clenow_ret.loc[common_cut],  w_kc),(kr_sv_ret.loc[common_cut],  w_kv)], initial)
        mf = metrics(eq_f, initial); mc = metrics(eq_c, initial)
        if mf and mc:
            print(f"  {label:28s} {mf['cagr_pct']:>+8.2f}% {mf['sharpe']:>+10.2f}   "
                  f"{mc['cagr_pct']:>+8.2f}% {mc['sharpe']:>+9.2f}   {mc['mdd_pct']:>+7.2f}% "
                  f"{mc['sharpe']-mf['sharpe']:>+8.3f}")

    df_cut_sweep.to_csv("backtest_results/kr/global_synthesis_sweep_excl.csv", index=False)
    print(f"\nSaved: backtest_results/kr/global_synthesis_sweep_excl.csv ({len(df_cut_sweep)} 조합)")


if __name__ == "__main__":
    main()
