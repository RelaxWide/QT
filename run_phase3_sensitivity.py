"""
Phase 3 민감도 분석: cloud_filter_thickness_min_pct
사용법: python run_phase3_sensitivity.py [--tickers AAPL MSFT ...]
"""
import argparse
import time
from pathlib import Path

import pandas as pd
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.regime import compute_regime
from src.strategy.hybrid import generate_hybrid_signals
from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics

THICK_VALS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    p1    = cfg["phase1_breakout_pullback"]
    p2    = cfg["phase2_cloud_support"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]

    tickers = args.tickers or get_sp500_tickers()
    print(f"Loading price data ({len(tickers)} tickers)...")
    price_data = fetch_all(tickers, start, end, min_bars=300, refresh=args.refresh)
    print(f"  {len(price_data)} symbols loaded")

    regime = compute_regime(
        start, end,
        ma_short=cfg["regime_filter"]["spy_ma_short"],
        ma_long=cfg["regime_filter"]["spy_ma_long"],
        vix_threshold=cfg["regime_filter"]["vix_threshold"],
    )

    results = []
    total = len(THICK_VALS)

    for i, thick in enumerate(THICK_VALS, 1):
        p2_mod = dict(p2)
        p2_mod["cloud_filter_thickness_min_pct"] = thick
        p2_mod["cloud_filter_use_chikou"] = False

        all_signals = []
        for sym, df in price_data.items():
            all_signals.extend(generate_hybrid_signals(sym, df, p1, p2_mod))
        all_signals.sort(key=lambda s: s.entry_date)

        if not all_signals:
            print(f"[{i}/{total}] thick={thick}%  — 시그널 없음")
            continue

        result  = run_backtest(all_signals, price_data, regime, cfg)
        m       = compute_metrics(result)

        g_trades = "✅" if m["total_trades"]    >= 100  else "❌"
        g_wr     = "✅" if m["win_rate"]         >= 0.33  else "❌"
        g_pf     = "✅" if m["profit_factor"]    >= 1.5   else "❌"
        g_mdd    = "✅" if m["max_drawdown_pct"] >= -15   else "❌"
        g_sh     = "✅" if m["sharpe"]           >= 0.8   else "❌"
        gates    = f"{g_trades}{g_wr}{g_pf}{g_mdd}{g_sh}"

        results.append({
            "thick_pct":      thick,
            "trades":         m["total_trades"],
            "win_rate":       m["win_rate"],
            "avg_r":          m["avg_r"],
            "profit_factor":  m["profit_factor"],
            "total_return":   m["total_return_pct"],
            "max_dd":         m["max_drawdown_pct"],
            "sharpe":         m["sharpe"],
            "sortino":        m["sortino"],
            "gates":          gates,
        })

        print(f"[{i}/{total}] thick={thick:4.1f}%  "
              f"T={m['total_trades']:4d}  WR={m['win_rate']:.1%}  "
              f"PF={m['profit_factor']:.2f}  Ret={m['total_return_pct']:6.1f}%  "
              f"MDD={m['max_drawdown_pct']:6.1f}%  Sharpe={m['sharpe']:.4f}  {gates}")

    if not results:
        return

    df_res = pd.DataFrame(results).sort_values("sharpe", ascending=False)
    out = Path("backtest_results/phase3_sensitivity.csv")
    df_res.to_csv(out, index=False)

    print(f"\n{'thick':>6} {'T':>5} {'WR':>6} {'avgR':>7} {'PF':>6} {'ret%':>7} {'MDD%':>7} {'sharpe':>7} {'sortino':>8}  gates")
    print("─" * 95)
    for _, row in df_res.iterrows():
        print(f"  {row['thick_pct']:>4.1f}  {int(row['trades']):>5}  {row['win_rate']:>5.1%}  "
              f"{row['avg_r']:>6.3f}  {row['profit_factor']:>5.2f}  "
              f"{row['total_return']:>6.1f}%  {row['max_dd']:>6.1f}%  "
              f"{row['sharpe']:>6.4f}  {row['sortino']:>7.4f}  {row['gates']}")

    print(f"\nResults saved: {out}")

    best = df_res.iloc[0]
    passing = df_res[
        (df_res["trades"] >= 100) &
        (df_res["win_rate"] >= 0.33) &
        (df_res["profit_factor"] >= 1.5) &
        (df_res["max_dd"] >= -15) &
        (df_res["sharpe"] >= 0.8)
    ]
    if not passing.empty:
        top = passing.sort_values("sharpe", ascending=False).iloc[0]
        print(f"\n✅ 전체 게이트 통과: thick={top['thick_pct']}%  Sharpe={top['sharpe']:.4f}")
        print(f"   config.yaml → cloud_filter_thickness_min_pct: {top['thick_pct']}")
    else:
        print(f"\n최고 Sharpe: thick={best['thick_pct']}%  Sharpe={best['sharpe']:.4f}")
        print("   Sharpe 0.8 미달 — chikou 필터 추가 또는 exit 구조 변경 검토 필요")


if __name__ == "__main__":
    main()
