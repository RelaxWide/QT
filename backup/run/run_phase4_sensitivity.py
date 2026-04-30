"""
Phase 4 팩터 조합 민감도 분석
사용법: python run_phase4_sensitivity.py [--tickers ...]
"""
import argparse
import time
from pathlib import Path

import pandas as pd
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.regime import compute_regime
from src.indicators.factors import build_factor_matrices
from src.strategy.factor_stack import generate_factor_signals
from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics

COMBOS = [
    # (label,          use_mom, use_bbw, use_rs, min_pass)
    ("Mom+RS (min2)",  True,    False,   True,   2),
    ("Mom only",       True,    False,   False,  1),
    ("RS only",        False,   False,   True,   1),
    ("All (min2)",     True,    True,    True,   2),
    ("All (min1)",     True,    True,    True,   1),
    ("Mom+BBW (min2)", True,    True,    False,  2),
    ("BBW only",       False,   True,    False,  1),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    p1    = cfg["phase1_breakout_pullback"]
    p2    = cfg["phase2_cloud_support"]
    p3    = cfg["phase3_hybrid"]
    p4    = cfg["phase4_factor_stack"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]

    p2_filter = dict(p2)
    p2_filter["cloud_filter_thickness_min_pct"] = p3["cloud_filter_thickness_min_pct"]
    p2_filter["cloud_filter_use_chikou"]        = p3["cloud_filter_use_chikou"]

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

    print("Building factor matrices...")
    mom_rank, bbw_rank, spy_mom = build_factor_matrices(
        price_data,
        mom_period=p4["momentum_period"],
        bb_period=p4["bbwidth_period"],
    )

    results = []
    total = len(COMBOS)

    for i, (label, use_mom, use_bbw, use_rs, min_pass) in enumerate(COMBOS, 1):
        f_cfg = dict(p4)
        f_cfg["use_momentum"]          = use_mom
        f_cfg["use_bbwidth"]           = use_bbw
        f_cfg["use_spy_rs"]            = use_rs
        f_cfg["min_factors_required"]  = min_pass

        all_signals = []
        for sym, df in price_data.items():
            all_signals.extend(
                generate_factor_signals(sym, df, p1, p2_filter, f_cfg, mom_rank, bbw_rank, spy_mom)
            )
        all_signals.sort(key=lambda s: s.entry_date)

        if not all_signals:
            print(f"[{i}/{total}] {label:20s} — 시그널 없음")
            continue

        result = run_backtest(all_signals, price_data, regime, cfg)
        m = compute_metrics(result)

        g = (
            ("✅" if m["total_trades"]    >= 100  else "❌") +
            ("✅" if m["win_rate"]         >= 0.33  else "❌") +
            ("✅" if m["profit_factor"]    >= 1.5   else "❌") +
            ("✅" if m["max_drawdown_pct"] >= -15   else "❌") +
            ("✅" if m["sharpe"]           >= 0.8   else "❌")
        )
        results.append({
            "label": label, "trades": m["total_trades"],
            "win_rate": m["win_rate"], "avg_r": m["avg_r"],
            "profit_factor": m["profit_factor"],
            "total_return": m["total_return_pct"],
            "max_dd": m["max_drawdown_pct"],
            "sharpe": m["sharpe"], "sortino": m["sortino"],
            "gates": g,
        })
        print(f"[{i}/{total}] {label:20s}  T={m['total_trades']:4d}  "
              f"WR={m['win_rate']:.1%}  PF={m['profit_factor']:.2f}  "
              f"Ret={m['total_return_pct']:6.1f}%  MDD={m['max_drawdown_pct']:6.1f}%  "
              f"Sharpe={m['sharpe']:.4f}  {g}")

    if not results:
        return

    df_res = pd.DataFrame(results).sort_values("sharpe", ascending=False)
    out = Path("backtest_results/phase4_sensitivity.csv")
    df_res.to_csv(out, index=False)

    print(f"\n{'조합':22s} {'T':>5} {'WR':>6} {'avgR':>7} {'PF':>6} {'ret%':>7} {'MDD%':>7} {'sharpe':>7} {'sortino':>8}  gates")
    print("─" * 100)
    for _, row in df_res.iterrows():
        print(f"  {row['label']:20s}  {int(row['trades']):>5}  {row['win_rate']:>5.1%}  "
              f"{row['avg_r']:>6.3f}  {row['profit_factor']:>5.2f}  "
              f"{row['total_return']:>6.1f}%  {row['max_dd']:>6.1f}%  "
              f"{row['sharpe']:>6.4f}  {row['sortino']:>7.4f}  {row['gates']}")

    passing = df_res[
        (df_res["trades"] >= 100) & (df_res["win_rate"] >= 0.33) &
        (df_res["profit_factor"] >= 1.5) & (df_res["max_dd"] >= -15) &
        (df_res["sharpe"] >= 0.8)
    ]
    print(f"\nResults saved: {out}")
    if not passing.empty:
        best = passing.sort_values("sharpe", ascending=False).iloc[0]
        print(f"✅ 게이트 통과 최적 조합: {best['label']}  Sharpe={best['sharpe']:.4f}")
    else:
        best = df_res.iloc[0]
        print(f"최고 Sharpe: {best['label']}  Sharpe={best['sharpe']:.4f}")
        print("→ Phase 3 유지 권장 (팩터 필터가 전반적으로 도움이 안 됨)")


if __name__ == "__main__":
    main()
