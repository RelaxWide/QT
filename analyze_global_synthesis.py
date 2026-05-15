"""
글로벌 합성 분석: US Clenow + Clenow KR + KW Super Value.

3자 가중 비율 sweep, KRW 통합 NAV 기준 (환율 1,400원 가정),
Sharpe / Calmar / MDD 최적 비율 탐색.

사용:
    python analyze_global_synthesis.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


FX_USD_KRW = 1400.0   # 가정 환율 (백테스트 동안 평균치)


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


def main():
    # 로드
    us_clenow = load_eq("backtest_results/clenow_equity.csv")
    kr_clenow = load_eq("backtest_results/kr/clenow_equity.csv")
    kr_sv     = load_eq("backtest_results/kr/kw_super_value_equity.csv")

    # 일별 수익률 (각자 자기 통화 기준)
    us_ret = us_clenow.pct_change().fillna(0)
    kr_clenow_ret = kr_clenow.pct_change().fillna(0)
    kr_sv_ret = kr_sv.pct_change().fillna(0)

    initial = 100_000.0   # 글로벌 NAV 초기값 (단위 무관, 통일)

    print("=" * 70)
    print("개별 전략 (정규화, initial=100,000)")
    print("=" * 70)
    for name, ret in [("US Clenow", us_ret), ("Clenow KR", kr_clenow_ret), ("KW Super Value", kr_sv_ret)]:
        common = ret.index
        eq = initial * (1 + ret.loc[common]).cumprod()
        m = metrics(eq, initial, name)
        if m:
            print(f"  {name:18s}: CAGR {m['cagr_pct']:>+6.2f}%  MDD {m['mdd_pct']:>+6.2f}%  "
                  f"Sharpe {m['sharpe']:.2f}  Calmar {m['calmar']:.2f}")

    # 상관계수 (공통 인덱스)
    common = us_ret.index.intersection(kr_clenow_ret.index).intersection(kr_sv_ret.index)
    df_ret = pd.DataFrame({
        "US_Clenow":   us_ret.loc[common],
        "Clenow_KR":   kr_clenow_ret.loc[common],
        "KW_SV":       kr_sv_ret.loc[common],
    })
    print()
    print("=" * 70)
    print("상관계수 매트릭스 (일간 수익률, 공통 기간)")
    print("=" * 70)
    print(df_ret.corr().round(3))

    print()
    print("=" * 70)
    print("3자 가중 sweep (Sharpe·Calmar 최적)")
    print("=" * 70)
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

    # 단독·합성 비교 표 (지정 비율들)
    interesting = [
        (1.0, 0.0, 0.0, "US 단독"),
        (0.0, 1.0, 0.0, "Clenow KR 단독"),
        (0.0, 0.0, 1.0, "KW SV 단독"),
        (0.5, 0.5, 0.0, "US 50 / KR Clenow 50"),
        (0.5, 0.0, 0.5, "US 50 / KW SV 50"),
        (0.0, 0.6, 0.4, "KR 60:40 (Clenow:SV)"),
        (0.5, 0.3, 0.2, "균형 50/30/20"),
        (0.4, 0.3, 0.3, "균형 40/30/30"),
        (0.6, 0.2, 0.2, "US 우선 60/20/20"),
        (0.33, 0.33, 0.34, "균등 1/3"),
    ]
    print(f"{'배분':30s} {'CAGR':>7s} {'MDD':>7s} {'Sharpe':>7s} {'Calmar':>7s}")
    for w_us, w_kc, w_kv, label in interesting:
        eq = synth_returns([
            (us_ret.loc[common], w_us),
            (kr_clenow_ret.loc[common], w_kc),
            (kr_sv_ret.loc[common], w_kv),
        ], initial)
        m = metrics(eq, initial)
        if m:
            print(f"  {label:28s} {m['cagr_pct']:>+6.2f}% {m['mdd_pct']:>+6.2f}% "
                  f"{m['sharpe']:>6.2f}  {m['calmar']:>6.2f}")

    print()
    print("=== 최적 Top 10 (Sharpe 기준) ===")
    top_sharpe = df.nlargest(10, "sharpe")
    print(top_sharpe[["w_us","w_clenow_kr","w_kw_sv","cagr_pct","mdd_pct","sharpe","calmar"]].to_string(index=False))

    print()
    print("=== 최적 Top 10 (Calmar 기준) ===")
    top_calmar = df.nlargest(10, "calmar")
    print(top_calmar[["w_us","w_clenow_kr","w_kw_sv","cagr_pct","mdd_pct","sharpe","calmar"]].to_string(index=False))

    df.to_csv("backtest_results/kr/global_synthesis_sweep.csv", index=False)
    print(f"\nSaved: backtest_results/kr/global_synthesis_sweep.csv ({len(df)} 조합)")


if __name__ == "__main__":
    main()
