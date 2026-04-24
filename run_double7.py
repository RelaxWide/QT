"""
Connors Double Seven 백테스트 (SPY/QQQ/IWM)
사용법: python run_double7.py [--refresh]
"""
import argparse
from pathlib import Path

import numpy as np
import yaml

from src.fetch.prices import fetch_all
from src.strategy.double7 import generate_double7_signals
from src.backtest.double7_engine import run_double7_backtest
from src.backtest.metrics import compute_metrics, save_report


TICKERS = ["SPY", "QQQ", "IWM"]


def compute_extra(result) -> dict:
    r_mults = [t.r_multiple for t in result.trades]
    if not r_mults:
        return {"tail_ratio": 0.0, "max_losing_streak": 0}
    arr  = np.array(r_mults)
    top5 = np.percentile(arr, 95)
    bot5 = abs(np.percentile(arr, 5))
    tail = (top5 / bot5) if bot5 > 0 else float("inf")
    streak = max_streak = 0
    for r in r_mults:
        if r <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return {"tail_ratio": round(tail, 4), "max_losing_streak": max_streak}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg    = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    d7_p   = cfg["double7_strategy"]
    start  = cfg["data"]["start_date"]
    end    = cfg["data"]["end_date"]

    print(f"Loading price data: {TICKERS}...")
    price_data = fetch_all(TICKERS, start, end, min_bars=220, refresh=args.refresh)
    print(f"  {len(price_data)} symbols loaded")

    print("Generating Double Seven signals...")
    all_signals = []
    for sym, df in price_data.items():
        sigs = generate_double7_signals(sym, df, d7_p)
        all_signals.extend(sigs)
        print(f"  {sym}: {len(sigs)} signals")
    all_signals.sort(key=lambda s: s.entry_date)
    print(f"  Total: {len(all_signals)} signals")

    if not all_signals:
        print("⚠️  시그널 없음")
        return

    print("Running Double Seven backtest...")
    result = run_double7_backtest(all_signals, price_data, cfg)
    print(f"  {len(result.trades)} trades closed")

    m     = compute_metrics(result)
    extra = compute_extra(result)
    save_report(m, result, output_dir="backtest_results", prefix="double7")

    print("\n── Connors Double Seven Results ──────────────────────")
    for k, v in m.items():
        if k != "exit_reasons":
            print(f"  {k:30s}: {v}")
    print(f"  {'tail_ratio':30s}: {extra['tail_ratio']}")
    print(f"  {'max_losing_streak':30s}: {extra['max_losing_streak']}")

    print("\n청산 사유:")
    for reason, cnt in m.get("exit_reasons", {}).items():
        print(f"  {reason:20s}: {cnt}")

    # Type 1 (평균회귀) 게이트
    print("\n게이트 체크 [Type 1 — Mean Reversion]:")
    gates = {
        "Trades ≥ 50":           m["total_trades"]    >= 50,
        "Win rate ≥ 70%":        m["win_rate"]         >= 0.70,
        "Profit Factor ≥ 1.8":   m["profit_factor"]    >= 1.8,
        "MDD ≥ -12%":            m["max_drawdown_pct"] >= -12,
        "Sharpe ≥ 1.0":          m["sharpe"]           >= 1.0,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")

    if all(gates.values()):
        print("\n✅ 전 게이트 통과")
    else:
        failed = [d for d, p in gates.items() if not p]
        print(f"\n❌ 미달 항목: {', '.join(failed)}")


if __name__ == "__main__":
    main()
