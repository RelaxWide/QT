"""
KR 트랙 합성 백테스트 분석.

Clenow KR + KW Super Value 가중 평균 NAV → Sharpe/MDD/Calmar 최적화.
Clenow KR + Super Quality / Super Value + Super Quality 도 비교.

사용:
    python analyze_correlation_kw.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


def load_eq(path: str) -> pd.Series:
    df = pd.read_csv(path, index_col="date", parse_dates=["date"])
    return df["equity"]


def metrics(eq: pd.Series, initial: float) -> dict:
    daily = eq.pct_change().dropna()
    n_years = len(eq) / 252
    cagr = (eq.iloc[-1] / initial) ** (1/n_years) - 1 if n_years > 0 else 0
    roll = eq.cummax()
    dd = (eq - roll) / roll
    mdd = float(dd.min())
    sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0
    sortino = daily.mean() / daily[daily < 0].std() * np.sqrt(252) if daily[daily<0].std() > 0 else 0
    calmar = cagr / abs(mdd) if mdd != 0 else 0
    return {
        "cagr_pct":         round(cagr*100, 2),
        "mdd_pct":          round(mdd*100, 2),
        "sharpe":           round(sharpe, 4),
        "sortino":          round(sortino, 4),
        "calmar":           round(calmar, 4),
        "total_return_pct": round((eq.iloc[-1]/initial - 1)*100, 2),
    }


def synth_equity(eq_a: pd.Series, eq_b: pd.Series, w_a: float, w_b: float, initial: float = 50_000_000) -> pd.Series:
    """가중 평균 NAV — 공통 인덱스만 사용. 일일 returns 가중합 → cumprod."""
    # 둘 다 같은 시작점에서 시작했다고 가정 (initial 동일)
    common = eq_a.index.intersection(eq_b.index)
    if len(common) == 0:
        return pd.Series(dtype=float)
    ra = eq_a.loc[common].pct_change().fillna(0)
    rb = eq_b.loc[common].pct_change().fillna(0)
    rc = w_a * ra + w_b * rb
    eq_c = initial * (1 + rc).cumprod()
    return eq_c


def main():
    # 로드
    clenow = load_eq("backtest_results/kr/clenow_equity.csv")
    sv     = load_eq("backtest_results/kr/kw_super_value_equity.csv")
    sq     = load_eq("backtest_results/kr/kw_super_quality_equity.csv")

    initial = 50_000_000.0

    print("=" * 70)
    print("개별 전략 성과 (KR, 12년 기준)")
    print("=" * 70)
    for name, eq in [("Clenow KR", clenow), ("KW Super Value", sv), ("KW Super Quality", sq)]:
        m = metrics(eq, initial)
        print(f"  {name:20s}: CAGR {m['cagr_pct']:>+6.2f}%  MDD {m['mdd_pct']:>+6.2f}%  "
              f"Sharpe {m['sharpe']:.2f}  Calmar {m['calmar']:.2f}")

    print()
    print("=" * 70)
    print("Clenow KR + Super Value 가중 sweep")
    print("=" * 70)
    best = {"sharpe": 0}
    sweep = []
    for w_clenow in np.arange(0, 1.01, 0.1):
        w_sv = 1 - w_clenow
        eq_c = synth_equity(clenow, sv, w_clenow, w_sv, initial)
        if eq_c.empty:
            continue
        m = metrics(eq_c, initial)
        m["w_clenow"] = round(float(w_clenow), 2)
        m["w_super_value"] = round(float(w_sv), 2)
        sweep.append(m)
        print(f"  Clenow {w_clenow:.0%} / SV {w_sv:.0%}: "
              f"CAGR {m['cagr_pct']:>+6.2f}%  MDD {m['mdd_pct']:>+6.2f}%  "
              f"Sharpe {m['sharpe']:.2f}  Calmar {m['calmar']:.2f}")
        if m["sharpe"] > best["sharpe"]:
            best = m

    print()
    print(f"최적 (Sharpe 기준): Clenow {best['w_clenow']:.0%} / SV {best['w_super_value']:.0%}")
    print(f"  CAGR {best['cagr_pct']:+.2f}% / MDD {best['mdd_pct']:+.2f}% / Sharpe {best['sharpe']:.2f}")

    # Calmar 기준 최적
    best_calmar = max(sweep, key=lambda r: r["calmar"])
    print(f"최적 (Calmar 기준): Clenow {best_calmar['w_clenow']:.0%} / SV {best_calmar['w_super_value']:.0%}")
    print(f"  CAGR {best_calmar['cagr_pct']:+.2f}% / MDD {best_calmar['mdd_pct']:+.2f}% / Calmar {best_calmar['calmar']:.2f}")

    print()
    print("=" * 70)
    print("Clenow KR + Super Quality (참고)")
    print("=" * 70)
    for w_clenow in [0.3, 0.5, 0.7]:
        w_sq = 1 - w_clenow
        eq_c = synth_equity(clenow, sq, w_clenow, w_sq, initial)
        m = metrics(eq_c, initial)
        print(f"  Clenow {w_clenow:.0%} / SQ {w_sq:.0%}: "
              f"CAGR {m['cagr_pct']:>+6.2f}%  MDD {m['mdd_pct']:>+6.2f}%  "
              f"Sharpe {m['sharpe']:.2f}  Calmar {m['calmar']:.2f}")

    print()
    print("=" * 70)
    print("Super Value + Super Quality (둘 다 펀더, 분산 효과 작을 듯)")
    print("=" * 70)
    for w_sv in [0.3, 0.5, 0.7]:
        w_sq = 1 - w_sv
        eq_c = synth_equity(sv, sq, w_sv, w_sq, initial)
        m = metrics(eq_c, initial)
        print(f"  SV {w_sv:.0%} / SQ {w_sq:.0%}: "
              f"CAGR {m['cagr_pct']:>+6.2f}%  MDD {m['mdd_pct']:>+6.2f}%  "
              f"Sharpe {m['sharpe']:.2f}  Calmar {m['calmar']:.2f}")

    # 결과 저장
    out = Path("backtest_results/kr/portfolio_synthesis_kw.json")
    out.write_text(json.dumps({
        "individual": {
            "clenow_kr":      metrics(clenow, initial),
            "super_value":    metrics(sv, initial),
            "super_quality":  metrics(sq, initial),
        },
        "sweep_clenow_sv": sweep,
        "best_sharpe":     best,
        "best_calmar":     best_calmar,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
