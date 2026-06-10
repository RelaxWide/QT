"""
KOSPI 2025-03 ~ 상승장 효과 제외 메트릭 재계산.

equity CSV 를 잘라서 CAGR/MDD/Sharpe/Sortino/Calmar 비교.
원본 백테스트와 차이로 상승장 부풀림 효과 가시화.

사용:
    python analyze_excl_2026.py
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pandas as pd


CUTOFF = pd.Timestamp("2025-02-28")   # KOSPI 본격 상승 시작 직전

STRATS = {
    "KW Super Value":     ("backtest_results/kr/kw_super_value_equity.csv", 50_000_000),
    "KW Super Quality":   ("backtest_results/kr/kw_super_quality_equity.csv", 50_000_000),
    "KW Soseongbelma":    ("backtest_results/kr/kw_soseongbelma_equity.csv", 50_000_000),
    "Clenow KR":          ("backtest_results/kr/clenow_equity.csv", 50_000_000),
    "Clenow US":          ("backtest_results/clenow_equity.csv", 100_000),
    "Weinstein US":       ("backtest_results/weinstein_equity.csv", 100_000),
}


def metrics(eq: pd.Series, initial: float) -> dict:
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
        "n_years":   round(n_years, 2),
        "final_nav": round(float(eq.iloc[-1]), 0),
        "cagr_pct":  round(cagr*100, 2),
        "mdd_pct":   round(mdd*100, 2),
        "sharpe":    round(sharpe, 3),
        "sortino":   round(sortino, 3),
        "calmar":    round(calmar, 3),
    }


def main():
    rows_full = []
    rows_cut  = []
    rows_2026 = []
    print("=" * 110)
    print(f"전략별 메트릭 비교: 전체 vs {CUTOFF.date()} 컷오프 (KOSPI 2025-03 상승장 제외)")
    print("=" * 110)
    for name, (path, cap) in STRATS.items():
        df = pd.read_csv(path, index_col="date", parse_dates=["date"])
        eq = df["equity"]

        m_full = metrics(eq, cap)
        eq_cut = eq[eq.index <= CUTOFF]
        m_cut  = metrics(eq_cut, cap)

        # 상승장 (2025-03 ~ 마지막) 단독 수익률
        if not eq_cut.empty and not eq.empty:
            ret_2026 = (eq.iloc[-1] / eq_cut.iloc[-1] - 1) * 100
        else:
            ret_2026 = 0.0

        m_full["strategy"] = name + " (2026 포함)"
        m_cut["strategy"]  = name + " (~2025)"
        rows_full.append(m_full)
        rows_cut.append(m_cut)

        delta_cagr   = m_full["cagr_pct"] - m_cut["cagr_pct"]
        delta_sharpe = m_full["sharpe"]   - m_cut["sharpe"]
        delta_mdd    = m_full["mdd_pct"]  - m_cut["mdd_pct"]
        rows_2026.append({
            "strategy":      name,
            "ret_2026_pct":  round(ret_2026, 2),
            "cagr_full":     m_full["cagr_pct"],
            "cagr_cut":      m_cut["cagr_pct"],
            "cagr_delta":    round(delta_cagr, 2),
            "sharpe_full":   m_full["sharpe"],
            "sharpe_cut":    m_cut["sharpe"],
            "sharpe_delta":  round(delta_sharpe, 3),
            "mdd_full":      m_full["mdd_pct"],
            "mdd_cut":       m_cut["mdd_pct"],
        })

    print()
    print(f"{'전략':25s} {'25-3~상승':>11s} {'CAGR 원본':>11s} {'CAGR 컷':>10s} {'Δ CAGR':>8s} "
          f"{'Sharpe 원본':>12s} {'Sharpe 컷':>11s} {'Δ Sharpe':>10s}")
    print("-" * 110)
    for r in rows_2026:
        print(f"{r['strategy']:25s} {r['ret_2026_pct']:>+9.2f}% {r['cagr_full']:>+9.2f}% "
              f"{r['cagr_cut']:>+8.2f}% {r['cagr_delta']:>+7.2f}% "
              f"{r['sharpe_full']:>+10.2f}   {r['sharpe_cut']:>+9.2f}   {r['sharpe_delta']:>+8.3f}")

    print()
    print("=" * 110)
    print(f"{CUTOFF.date()} 컷오프 (KOSPI 2025-03 상승장 제외) 상세")
    print("=" * 110)
    print(f"{'전략':25s} {'기간':>7s} {'CAGR':>8s} {'MDD':>8s} {'Sharpe':>8s} {'Sortino':>8s} {'Calmar':>8s}")
    print("-" * 80)
    for r in rows_cut:
        print(f"{r['strategy']:25s} {r['n_years']:>5.1f}년  {r['cagr_pct']:>+6.2f}% {r['mdd_pct']:>+6.2f}% "
              f"{r['sharpe']:>6.2f}  {r['sortino']:>6.2f}  {r['calmar']:>6.2f}")

    # KOSPI 인덱스 자체 (^KS11) 의 상승장 기여도
    print()
    print("=" * 110)
    print("벤치마크: KOSPI ^KS11 & SPY")
    print("=" * 110)
    try:
        ks = pd.read_parquet("data/raw/kr/_KS11.parquet")
        ks = ks[(ks.index >= "2015-01-01")]
        c_start = ks["close"].iloc[0]
        c_cut   = ks.loc[ks.index <= CUTOFF, "close"].iloc[-1]
        c_end   = ks["close"].iloc[-1]
        ret_2025_full = (c_end/c_start - 1)*100
        ret_2025_cut  = (c_cut/c_start - 1)*100
        ret_uplift    = (c_end/c_cut - 1)*100
        years_full = len(ks)/252
        years_cut  = len(ks[ks.index <= CUTOFF])/252
        cagr_full = ((c_end/c_start) ** (1/years_full) - 1)*100
        cagr_cut  = ((c_cut/c_start) ** (1/years_cut) - 1)*100
        print(f"KOSPI  전체 {years_full:.1f}년: CAGR {cagr_full:+.2f}%  /  컷 {years_cut:.1f}년: CAGR {cagr_cut:+.2f}%  /  상승장({CUTOFF.date()}~) {ret_uplift:+.2f}%")
    except Exception as e:
        print(f"KOSPI 데이터 로드 실패: {e}")

    # JSON 저장
    out = {"cutoff": str(CUTOFF.date()), "rows_full": rows_full, "rows_cut": rows_cut, "rows_2026": rows_2026}
    Path("backtest_results/excl_2026_analysis.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nSaved: backtest_results/excl_2026_analysis.json")


if __name__ == "__main__":
    main()
