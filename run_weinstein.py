"""
Weinstein Stage 2 백테스트 (S&P500 개별주, 주봉)
사용법: python run_weinstein.py [--tickers ...] [--refresh]
"""
import argparse
import time
from pathlib import Path

import numpy as np
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.strategy.weinstein_stage2 import generate_weinstein_signals
from src.backtest.weinstein_engine import run_weinstein_backtest
from src.backtest.metrics import compute_metrics, save_report, compute_rotation_metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    w_p   = cfg["weinstein_strategy"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]

    tickers = args.tickers or get_sp500_tickers()
    if "SPY" not in tickers:
        tickers = ["SPY"] + list(tickers)

    min_bars = w_p["ma30_period"] * 5 + 10
    print(f"Loading price data ({len(tickers)} tickers)...")
    t0 = time.time()
    price_data = fetch_all(tickers, start, end, min_bars=min_bars, refresh=args.refresh)
    print(f"  {len(price_data)} symbols loaded in {time.time()-t0:.1f}s")

    if "SPY" not in price_data:
        print("❌ SPY 없음"); return

    print("Generating Weinstein Stage 2 signals...")
    all_signals = []
    for sym, df in price_data.items():
        if sym == "SPY":
            continue
        sigs = generate_weinstein_signals(sym, df, w_p)
        all_signals.extend(sigs)
    all_signals.sort(key=lambda s: s.entry_date)
    print(f"  {len(all_signals)} signals")

    if not all_signals:
        print("⚠️  시그널 없음"); return

    print("Running Weinstein backtest...")
    spy_close = price_data["SPY"]["close"]
    result = run_weinstein_backtest(all_signals, price_data, spy_close, cfg)
    print(f"  {len(result.trades)} trades closed")

    m = compute_metrics(result)
    save_report(m, result, output_dir="backtest_results", prefix="weinstein")
    em = compute_rotation_metrics(result.equity_curve, cap)

    print("\n── Weinstein Stage 2 Results ─────────────────────────")
    for k, v in m.items():
        if k != "exit_reasons":
            print(f"  {k:30s}: {v}")
    print(f"\n  {'cagr_pct':30s}: {em['cagr_pct']}")
    print(f"  {'monthly_win_rate':30s}: {em['monthly_win_rate']}")

    print("\n청산 사유:")
    for reason, cnt in m.get("exit_reasons", {}).items():
        print(f"  {reason:20s}: {cnt}")

    print("\n게이트 체크:")
    gates = {
        "Trades ≥ 100":      m["total_trades"]    >= 100,
        "Profit Factor ≥ 1.5": m["profit_factor"]  >= 1.5,
        "MDD ≥ -25%":        m["max_drawdown_pct"] >= -25,
        "Sharpe ≥ 0.7":      m["sharpe"]           >= 0.7,
        "CAGR ≥ 8%":         em["cagr_pct"]        >= 8.0,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")
    if all(gates.values()):
        print("\n✅ 전 게이트 통과")
    else:
        print(f"\n❌ 미달: {', '.join(d for d, p in gates.items() if not p)}")


if __name__ == "__main__":
    main()
