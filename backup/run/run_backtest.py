"""
Phase 1: Breakout Pullback 백테스트 실행 스크립트
사용법: python run_backtest.py [--phase 1] [--refresh]
"""
import argparse
import time
from pathlib import Path

import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.regime import compute_regime
from src.strategy.breakout_pullback import generate_signals
from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics, save_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", type=int, default=1)
    parser.add_argument("--refresh", action="store_true", help="Force re-download price data")
    parser.add_argument("--tickers", nargs="*", help="Limit to specific tickers (for testing)")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    data_cfg = cfg["data"]
    p1 = cfg["phase1_breakout_pullback"]

    start = data_cfg["start_date"]
    end = data_cfg["end_date"]  # None = today

    # ── 1. Universe ────────────────────────────────────────────────────────
    if args.tickers:
        tickers = args.tickers
    else:
        print("Fetching S&P 500 ticker list...")
        tickers = get_sp500_tickers()
    print(f"Universe: {len(tickers)} tickers | {start} → {end or 'today'}")

    # ── 2. Price data ─────────────────────────────────────────────────────
    price_data = fetch_all(tickers, start, end, min_bars=252, refresh=args.refresh)
    print(f"Loaded {len(price_data)} symbols with sufficient history.")

    # ── 3. Regime filter ──────────────────────────────────────────────────
    print("Computing market regime (SPY + VIX)...")
    regime = compute_regime(
        start, end,
        ma_short=cfg["regime_filter"]["spy_ma_short"],
        ma_long=cfg["regime_filter"]["spy_ma_long"],
        vix_threshold=cfg["regime_filter"]["vix_threshold"],
    )

    # ── 4. Signal generation ──────────────────────────────────────────────
    print("Generating breakout pullback signals...")
    t0 = time.time()
    all_signals = []
    for sym, df in price_data.items():
        sigs = generate_signals(sym, df, p1)
        all_signals.extend(sigs)

    all_signals.sort(key=lambda s: s.entry_date)
    print(f"  {len(all_signals)} signals in {time.time() - t0:.1f}s")

    # ── 5. Backtest ───────────────────────────────────────────────────────
    print("Running backtest...")
    t0 = time.time()
    result = run_backtest(all_signals, price_data, regime, cfg)
    print(f"  Done in {time.time() - t0:.1f}s | {len(result.trades)} trades closed")

    # ── 6. Metrics & report ───────────────────────────────────────────────
    metrics = compute_metrics(result)
    save_report(metrics, result, output_dir="backtest_results")

    print("\n── Phase 1 Results ──────────────────────────────────")
    for k, v in metrics.items():
        if k != "exit_reasons":
            print(f"  {k:30s}: {v}")
    print("\nPhase 게이트:")
    gates = {
        "Trades ≥ 100": metrics["total_trades"] >= 100,
        "Win rate ≥ 45%": metrics["win_rate"] >= 0.45,
        "Profit Factor ≥ 1.3": metrics["profit_factor"] >= 1.3,
        "MDD ≥ -15%": metrics["max_drawdown_pct"] >= -15,
        "Sharpe ≥ 0.8": metrics["sharpe"] >= 0.8,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")

    all_passed = all(gates.values())
    if all_passed:
        print("\n✅ Phase 1 게이트 통과 — Phase 2 진행 가능")
    else:
        print("\n❌ Phase 1 게이트 미달 — 파라미터 조정 후 재실행 필요")


if __name__ == "__main__":
    main()
