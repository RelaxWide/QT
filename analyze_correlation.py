"""
Clenow + Weinstein 상관계수 + 포트폴리오 합성 분석

두 전략의 equity curve를 로드해서:
  1) 일별/월별 수익률 상관계수
  2) 단순 50:50 합성 포트폴리오 성과
  3) 다양한 비중 조합 최적화 (MDD 최소, Sharpe 최대)

사용법:
  python analyze_correlation.py
"""
import argparse
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

matplotlib.rcParams["font.family"] = "DejaVu Sans"


def load_equity(path: str) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    return df["equity"]


def metrics(equity: pd.Series, initial: float = 100000.0) -> dict:
    rets = equity.pct_change().dropna()
    n_years = len(equity) / 252
    total_ret = equity.iloc[-1] / initial - 1
    cagr = (equity.iloc[-1] / initial) ** (1 / n_years) - 1 if n_years > 0 else 0
    sharpe = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0
    downside = rets[rets < 0]
    sortino = rets.mean() / downside.std() * np.sqrt(252) if len(downside) > 0 and downside.std() > 0 else 0
    rolling_max = equity.cummax()
    dd = (equity - rolling_max) / rolling_max
    mdd = dd.min()
    return {
        "total_return_pct": total_ret * 100,
        "cagr_pct":         cagr * 100,
        "sharpe":           sharpe,
        "sortino":          sortino,
        "max_drawdown_pct": mdd * 100,
        "annual_vol_pct":   rets.std() * np.sqrt(252) * 100,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extended", action="store_true",
                        help="2000~ 확장 기간 분석 (clenow_ext_equity.csv, weinstein_ext_equity.csv 사용)")
    args = parser.parse_args()

    results_dir = Path("backtest_results")
    suffix = "_ext" if args.extended else ""
    clenow_path = results_dir / f"clenow{suffix}_equity.csv"
    weinstein_path = results_dir / f"weinstein{suffix}_equity.csv"

    if not clenow_path.exists():
        msg = "run_validation.py --method period" if args.extended else "run_clenow.py"
        print(f"{clenow_path.name} 없음 — 먼저 'python {msg}' 실행")
        return
    if not weinstein_path.exists():
        msg = "run_validation.py --method period" if args.extended else "run_weinstein.py"
        print(f"{weinstein_path.name} 없음 — 먼저 'python {msg}' 실행")
        return

    period_label = "2000-현재 (확장)" if args.extended else "2015-현재"
    print(f"── 분석 기간: {period_label} ──")

    clenow    = load_equity(clenow_path).rename("Clenow")
    weinstein = load_equity(weinstein_path).rename("Weinstein")

    # 공통 기간으로 정렬
    df = pd.concat([clenow, weinstein], axis=1).dropna()
    print(f"공통 기간: {df.index[0].date()} ~ {df.index[-1].date()}  ({len(df)} 거래일)")

    # 일별 수익률
    rets = df.pct_change().dropna()

    # ── 1. 상관계수 ───────────────────────────────────────────────────
    daily_corr = rets["Clenow"].corr(rets["Weinstein"])

    # 월별 수익률 (월말 기준)
    monthly = df.resample("ME").last()
    monthly_rets = monthly.pct_change().dropna()
    monthly_corr = monthly_rets["Clenow"].corr(monthly_rets["Weinstein"])

    print("\n── 상관계수 ─────────────────────────────────────────────")
    print(f"  일별 수익률 corr  : {daily_corr:.4f}")
    print(f"  월별 수익률 corr  : {monthly_corr:.4f}")
    if daily_corr < 0.3:
        print("  → 매우 낮음. 합성 시 분산효과 큼")
    elif daily_corr < 0.6:
        print("  → 중간. 합성 시 일부 분산효과 기대")
    else:
        print("  → 높음. 합성 시 분산효과 제한적")

    # ── 2. 개별 + 50:50 합성 ──────────────────────────────────────────
    # 합성: 각 전략에 동일 자본 → 일별 수익률 평균
    combined_rets_5050 = (rets["Clenow"] + rets["Weinstein"]) / 2
    combined_equity_5050 = (1 + combined_rets_5050).cumprod() * 100000

    print("\n── 개별 vs 50:50 합성 성과 ───────────────────────────────")
    header = f"{'전략':<20} {'CAGR':>8} {'Sharpe':>8} {'Sortino':>8} {'MDD':>8} {'Vol':>8}"
    print(header)
    print("-" * len(header))
    for name, eq in [("Clenow",       clenow.reindex(df.index)),
                     ("Weinstein",    weinstein.reindex(df.index)),
                     ("50:50 합성",   combined_equity_5050)]:
        m = metrics(eq)
        print(f"{name:<20} {m['cagr_pct']:>7.2f}% {m['sharpe']:>8.3f} {m['sortino']:>8.3f} "
              f"{m['max_drawdown_pct']:>7.2f}% {m['annual_vol_pct']:>7.2f}%")

    # ── 3. 비중 스윕 (Clenow 0~100%, 5% 단위) ─────────────────────────
    print("\n── 비중 스윕 (Clenow 비중 vs 합성 성과) ──────────────────")
    sweep = []
    for w_c in np.arange(0, 1.001, 0.05):
        w_w = 1 - w_c
        cr = w_c * rets["Clenow"] + w_w * rets["Weinstein"]
        eq = (1 + cr).cumprod() * 100000
        m = metrics(eq)
        sweep.append({
            "clenow_w":  w_c,
            "weinstein_w": w_w,
            **m,
        })
    sweep_df = pd.DataFrame(sweep)

    # 핵심만 출력
    print(f"{'Clenow%':>8} {'Wein%':>8} {'CAGR':>8} {'Sharpe':>8} {'MDD':>8}")
    for _, r in sweep_df.iterrows():
        if int(r['clenow_w'] * 100) % 10 == 0:
            print(f"{r['clenow_w']*100:>7.0f}% {r['weinstein_w']*100:>7.0f}% "
                  f"{r['cagr_pct']:>7.2f}% {r['sharpe']:>8.3f} {r['max_drawdown_pct']:>7.2f}%")

    # 최적 조합
    best_sharpe = sweep_df.loc[sweep_df["sharpe"].idxmax()]
    best_mdd    = sweep_df.loc[sweep_df["max_drawdown_pct"].idxmax()]  # 가장 덜 음수
    best_calmar = sweep_df.assign(
        calmar=sweep_df["cagr_pct"] / (-sweep_df["max_drawdown_pct"])
    )
    best_calmar = best_calmar.loc[best_calmar["calmar"].idxmax()]
    print(f"\n── 최적 조합 ────────────────────────────────────────────")
    print(f"  Sharpe 최대   : Clenow {best_sharpe['clenow_w']*100:.0f}% / Weinstein {best_sharpe['weinstein_w']*100:.0f}%  "
          f"→ Sharpe={best_sharpe['sharpe']:.3f}, CAGR={best_sharpe['cagr_pct']:.2f}%, MDD={best_sharpe['max_drawdown_pct']:.2f}%")
    print(f"  MDD 최소     : Clenow {best_mdd['clenow_w']*100:.0f}% / Weinstein {best_mdd['weinstein_w']*100:.0f}%  "
          f"→ MDD={best_mdd['max_drawdown_pct']:.2f}%, CAGR={best_mdd['cagr_pct']:.2f}%, Sharpe={best_mdd['sharpe']:.3f}")
    print(f"  Calmar 최대  : Clenow {best_calmar['clenow_w']*100:.0f}% / Weinstein {best_calmar['weinstein_w']*100:.0f}%  "
          f"→ Calmar={best_calmar['calmar']:.3f}, CAGR={best_calmar['cagr_pct']:.2f}%, MDD={best_calmar['max_drawdown_pct']:.2f}%")

    # CSV 저장
    sweep_csv = results_dir / "correlation_sweep.csv"
    sweep_df.to_csv(sweep_csv, index=False)
    print(f"\n비중 스윕 CSV: {sweep_csv}")

    # ── 4. 차트 ──────────────────────────────────────────────────────
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))

    # 개별 + 합성 equity curve
    ax = axes[0, 0]
    ax.plot(clenow.index,    clenow / clenow.iloc[0] * 100000, label="Clenow", color="#1f77b4")
    ax.plot(weinstein.index, weinstein / weinstein.iloc[0] * 100000, label="Weinstein", color="#ff7f0e")
    ax.plot(combined_equity_5050.index, combined_equity_5050, label="50:50 합성", color="#2ca02c", linewidth=2)
    ax.set_title("Equity Curve"); ax.set_ylabel("Equity ($)")
    ax.legend(); ax.grid(alpha=0.3); ax.set_yscale("log")

    # Drawdown 비교
    ax = axes[0, 1]
    for name, eq, color in [("Clenow", clenow.reindex(df.index), "#1f77b4"),
                            ("Weinstein", weinstein.reindex(df.index), "#ff7f0e"),
                            ("50:50 합성", combined_equity_5050, "#2ca02c")]:
        dd = (eq / eq.cummax() - 1) * 100
        ax.fill_between(dd.index, dd, 0, alpha=0.3, color=color, label=name)
    ax.set_title("Drawdown"); ax.set_ylabel("DD (%)")
    ax.legend(); ax.grid(alpha=0.3)

    # 비중 스윕 (CAGR vs MDD)
    ax = axes[1, 0]
    sc = ax.scatter(-sweep_df["max_drawdown_pct"], sweep_df["cagr_pct"],
                    c=sweep_df["sharpe"], cmap="viridis", s=60)
    for _, r in sweep_df.iterrows():
        if int(r['clenow_w'] * 100) % 20 == 0:
            ax.annotate(f"C{int(r['clenow_w']*100)}%",
                        (-r["max_drawdown_pct"], r["cagr_pct"]),
                        fontsize=7, ha="center")
    plt.colorbar(sc, ax=ax, label="Sharpe")
    ax.set_xlabel("MDD (%, 절댓값)"); ax.set_ylabel("CAGR (%)")
    ax.set_title("비중 스윕 (Risk-Return)"); ax.grid(alpha=0.3)

    # 비중에 따른 Sharpe / Calmar
    ax = axes[1, 1]
    calmar_arr = sweep_df["cagr_pct"] / (-sweep_df["max_drawdown_pct"])
    ax.plot(sweep_df["clenow_w"] * 100, sweep_df["sharpe"], marker="o", label="Sharpe")
    ax2 = ax.twinx()
    ax2.plot(sweep_df["clenow_w"] * 100, calmar_arr, marker="s", color="orange", label="Calmar")
    ax.set_xlabel("Clenow 비중 (%)"); ax.set_ylabel("Sharpe")
    ax2.set_ylabel("Calmar", color="orange")
    ax.set_title("비중 vs Sharpe / Calmar"); ax.grid(alpha=0.3)
    ax.legend(loc="upper left"); ax2.legend(loc="upper right")

    plt.suptitle(f"Clenow + Weinstein 합성 분석 | 일별 corr={daily_corr:.3f}, 월별 corr={monthly_corr:.3f}",
                 fontsize=12)
    plt.tight_layout()

    png = results_dir / f"correlation_analysis{suffix}.png"
    plt.savefig(png, dpi=120, bbox_inches="tight")
    print(f"차트: {png}")
    plt.show()


if __name__ == "__main__":
    main()
