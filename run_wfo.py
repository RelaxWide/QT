"""
Walk-Forward Validation — Phase 1 / Phase 3 공용
사용법:
  python run_wfo.py           # Phase 1
  python run_wfo.py --phase 3 # Phase 3 Hybrid
"""
import argparse
from pathlib import Path

import pandas as pd
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.regime import compute_regime
from src.backtest.engine import run_backtest, BacktestResult
from src.backtest.metrics import compute_metrics


def build_signals(phase: int, price_data: dict, cfg: dict) -> list:
    if phase == 1:
        from src.strategy.breakout_pullback import generate_signals
        p1 = cfg["phase1_breakout_pullback"]
        sigs = []
        for sym, df in price_data.items():
            sigs.extend(generate_signals(sym, df, p1))

    elif phase == 3:
        from src.strategy.hybrid import generate_hybrid_signals
        p1 = cfg["phase1_breakout_pullback"]
        p2 = cfg["phase2_cloud_support"]
        p3 = cfg["phase3_hybrid"]
        p2_filter = dict(p2)
        p2_filter["cloud_filter_thickness_min_pct"] = p3["cloud_filter_thickness_min_pct"]
        p2_filter["cloud_filter_use_chikou"]        = p3["cloud_filter_use_chikou"]
        sigs = []
        for sym, df in price_data.items():
            sigs.extend(generate_hybrid_signals(sym, df, p1, p2_filter))

    else:  # phase == 4
        from src.strategy.factor_stack import generate_factor_signals
        from src.indicators.factors import build_factor_matrices
        p1 = cfg["phase1_breakout_pullback"]
        p2 = cfg["phase2_cloud_support"]
        p3 = cfg["phase3_hybrid"]
        p4 = cfg["phase4_factor_stack"]
        p2_filter = dict(p2)
        p2_filter["cloud_filter_thickness_min_pct"] = p3["cloud_filter_thickness_min_pct"]
        p2_filter["cloud_filter_use_chikou"]        = p3["cloud_filter_use_chikou"]
        mom_rank, bbw_rank, spy_mom = build_factor_matrices(
            price_data,
            mom_period=p4["momentum_period"],
            bb_period=p4["bbwidth_period"],
        )
        sigs = []
        for sym, df in price_data.items():
            sigs.extend(generate_factor_signals(sym, df, p1, p2_filter, p4, mom_rank, bbw_rank, spy_mom))

    sigs.sort(key=lambda s: s.entry_date)
    return sigs


def approx_pf(rs: list) -> float:
    wins   = sum(r for r in rs if r > 0)
    losses = abs(sum(r for r in rs if r <= 0))
    return wins / losses if losses > 0 else float("inf")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int, default=1, choices=[1, 3, 4])
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    label = f"Phase {args.phase}"

    print(f"=== {label} WFO ===")

    tickers = args.tickers or get_sp500_tickers()
    print(f"Loading cached price data ({len(tickers)} tickers)...")
    price_data = fetch_all(tickers, start, end, min_bars=252, refresh=args.refresh)
    print(f"  {len(price_data)} symbols")

    regime = compute_regime(
        start, end,
        ma_short=cfg["regime_filter"]["spy_ma_short"],
        ma_long=cfg["regime_filter"]["spy_ma_long"],
        vix_threshold=cfg["regime_filter"]["vix_threshold"],
    )

    print(f"Generating signals...")
    all_signals = build_signals(args.phase, price_data, cfg)
    print(f"  {len(all_signals)} signals")

    print("Running full-period backtest...")
    full_result  = run_backtest(all_signals, price_data, regime, cfg)
    full_metrics = compute_metrics(full_result)
    pf_is = full_metrics["profit_factor"]
    print(f"  Full: Sharpe={full_metrics['sharpe']:.3f}  PF={pf_is:.2f}  "
          f"Ret={full_metrics['total_return_pct']:.1f}%  MDD={full_metrics['max_drawdown_pct']:.1f}%\n")

    # ── Year-by-year ──────────────────────────────────────────────────────
    trades_by_year: dict[int, list] = {}
    for t in full_result.trades:
        trades_by_year.setdefault(t.entry_date.year, []).append(t)

    eq      = full_result.equity_curve
    initial = cfg["backtest"]["initial_capital_usd"]

    print(f"{'Year':>6} {'Trades':>6} {'WR':>6} {'PF':>5} {'Ret%':>6} {'MDD%':>6} {'Sharpe':>7}  WFO≥50%")
    print("─" * 68)

    wfo_rows = []
    for year in sorted(trades_by_year.keys()):
        year_trades = trades_by_year[year]
        year_eq     = eq[eq.index.year == year]
        if year_eq.empty or not year_trades:
            continue

        m = compute_metrics(BacktestResult(
            trades=year_trades,
            equity_curve=year_eq,
            initial_capital=year_eq.iloc[0],
        ))
        if m.get("error"):
            continue

        wfo_ratio = m["profit_factor"] / pf_is if pf_is > 0 else 0
        passed    = wfo_ratio >= 0.5
        wfo_rows.append({
            "year": year, "wfo_ratio": wfo_ratio,
            **{k: v for k, v in m.items() if k != "exit_reasons"},
        })

        print(f"{year:>6} {m['total_trades']:>6} {m['win_rate']:>6.1%}"
              f" {m['profit_factor']:>5.2f} {m['total_return_pct']:>6.1f}"
              f" {m['max_drawdown_pct']:>6.1f} {m['sharpe']:>7.3f}"
              f"  {'✅' if passed else '❌'} ({wfo_ratio:.2f})")

    # ── Rolling 3yr-train → 1yr-test ─────────────────────────────────────
    years = sorted(trades_by_year.keys())
    print("\n── Rolling 3yr-train → 1yr-test ─────────────────────────────────")
    print(f"{'Test Yr':>8} {'PF(IS)':>8} {'PF(OOS)':>8} {'OOS/IS':>8}  Pass?")
    print("─" * 50)

    wfo_pass = wfo_total = 0
    for test_year in years:
        train_years  = [y for y in years if test_year - 3 <= y < test_year]
        if len(train_years) < 2:
            continue
        train_trades = [t for y in train_years for t in trades_by_year.get(y, [])]
        test_trades  = trades_by_year.get(test_year, [])
        if not train_trades or not test_trades:
            continue

        pf_train = approx_pf([t.r_multiple for t in train_trades])
        pf_test  = approx_pf([t.r_multiple for t in test_trades])
        ratio    = pf_test / pf_train if pf_train > 0 else 0
        passed   = ratio >= 0.5

        wfo_total += 1
        wfo_pass  += int(passed)
        print(f"{test_year:>8} {pf_train:>8.2f} {pf_test:>8.2f} {ratio:>8.2f}  {'✅' if passed else '❌'}")

    # ── Summary ───────────────────────────────────────────────────────────
    rate = wfo_pass / wfo_total if wfo_total > 0 else 0
    print(f"\nWFO 통과율: {wfo_pass}/{wfo_total} ({rate:.0%})")

    if rate >= 0.7:
        next_step = f"Phase {args.phase + 1} 진행 가능" if args.phase < 4 else "Paper Trading 준비 단계로 진입 가능"
        print(f"✅ WFO 검증 통과 (≥70%) — {next_step}")
    else:
        print("❌ WFO 검증 미달 — 특정 연도에 큰 성과 변동 있음, 주의 필요")

    out = Path("backtest_results")
    out.mkdir(exist_ok=True)
    csv_path = out / f"phase{args.phase}_wfo.csv"
    pd.DataFrame(wfo_rows).to_csv(csv_path, index=False)
    print(f"결과 저장: {csv_path}")


if __name__ == "__main__":
    main()
