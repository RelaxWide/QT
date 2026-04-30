"""
Meb Faber 10-Month TAA 백테스트
사용법: python run_faber.py [--refresh]
"""
import argparse
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
import yaml

from src.fetch.prices import fetch_all
from src.strategy.faber_taa import generate_faber_signals
from src.backtest.faber_engine import run_faber_backtest, compute_taa_metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg      = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    fb_p     = cfg["faber_taa"]
    start    = cfg["data"]["start_date"]
    end      = cfg["data"]["end_date"]
    universe    = fb_p["universe"]
    sma_m       = fb_p.get("sma_months", 10)
    cash_proxy  = fb_p.get("cash_proxy", "SHY")
    capital     = cfg["backtest"]["initial_capital_usd"]

    # ── 1. 데이터 ─────────────────────────────────────────────────────────
    load_tickers = list(dict.fromkeys(universe + [cash_proxy]))  # 중복 제거
    print(f"Loading TAA universe: {load_tickers}")
    price_data = fetch_all(load_tickers, start, end, min_bars=50, refresh=args.refresh)
    print(f"  {len(price_data)}/{len(universe)} ETFs loaded")

    missing = [s for s in universe if s not in price_data]
    if missing:
        print(f"  ⚠️  누락: {missing}")

    # ── 2. 월별 신호 생성 ─────────────────────────────────────────────────
    signals = generate_faber_signals(price_data, universe, sma_months=sma_m)
    print(f"  {len(signals)} monthly signals generated")

    if not signals:
        print("⚠️  신호 없음")
        return

    # ── 3. 백테스트 ───────────────────────────────────────────────────────
    equity, monthly_df = run_faber_backtest(signals, price_data, cfg)
    m = compute_taa_metrics(equity, capital)

    # ── 4. 결과 출력 ──────────────────────────────────────────────────────
    print("\n── Faber 10M TAA Results ──────────────────────────────")
    print(f"  {'Total return':30s}: {m['total_return_pct']:.1f}%")
    print(f"  {'CAGR':30s}: {m['cagr_pct']:.2f}%")
    print(f"  {'Max drawdown':30s}: {m['max_drawdown_pct']:.2f}%")
    print(f"  {'Max drawdown (days)':30s}: {m['max_drawdown_days']}")
    print(f"  {'Sharpe':30s}: {m['sharpe']}")
    print(f"  {'Sortino':30s}: {m['sortino']}")
    print(f"  {'Calmar':30s}: {m['calmar']}")
    print(f"  {'Monthly win rate':30s}: {m['monthly_win_rate']:.1%}")
    print(f"  {'Monthly observations':30s}: {m['monthly_observations']}")

    # 월별 비중 요약
    print("\n리밸런싱 요약 (최근 6개월):")
    if not monthly_df.empty:
        for _, row in monthly_df.tail(6).iterrows():
            print(f"  {str(row['rebalance_date'])[:10]} | IN: {row['assets_in']} | OUT: {row['assets_out']}")

    # ── 5. 게이트 체크 ────────────────────────────────────────────────────
    print("\n게이트 체크 (TAA 기준):")
    gates = {
        "CAGR ≥ 7%":        m["cagr_pct"]          >= 7.0,
        "MDD ≥ -20%":       m["max_drawdown_pct"]   >= -20,
        "Sharpe ≥ 0.5":     m["sharpe"]             >= 0.5,
        "Monthly WR ≥ 55%": m["monthly_win_rate"]   >= 0.55,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")

    if all(gates.values()):
        print("\n✅ 게이트 통과 — Phase 4와 상관관계 분석 진행 가능")
    else:
        print("\n❌ 게이트 미달")

    # ── 6. 차트 저장 ──────────────────────────────────────────────────────
    out = Path("backtest_results")
    out.mkdir(exist_ok=True)

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]})
    axes[0].plot(equity.index, equity.values, linewidth=1.2, color="#2196F3", label="Faber TAA")
    axes[0].axhline(capital, color="gray", linestyle="--", linewidth=0.8)
    axes[0].set_title(f"Meb Faber 10M TAA — CAGR {m['cagr_pct']:.1f}% | MDD {m['max_drawdown_pct']:.1f}% | Sharpe {m['sharpe']:.2f}")
    axes[0].set_ylabel("Portfolio Value ($)")
    axes[0].legend()
    axes[0].grid(alpha=0.3)

    dd = (equity - equity.cummax()) / equity.cummax() * 100
    axes[1].fill_between(dd.index, dd.values, 0, color="#F44336", alpha=0.5)
    axes[1].set_ylabel("Drawdown (%)")
    axes[1].set_xlabel("Date")
    axes[1].grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(out / "faber_taa_equity.png", dpi=150)
    plt.close()

    equity.to_csv(out / "faber_taa_equity.csv", header=True)
    monthly_df.to_csv(out / "faber_taa_monthly.csv", index=False)
    print(f"\nCharts & CSV → {out}/")


if __name__ == "__main__":
    main()
