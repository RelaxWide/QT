"""
Connors RSI(2) on SPY 백테스트
사용법: python run_rsi2_spy.py [--refresh]
"""
import argparse
from pathlib import Path
import numpy as np
import yaml

from src.fetch.prices import fetch_all
from src.strategy.rsi2_spy import generate_rsi2_signals
from src.backtest.rsi2_engine import run_rsi2_backtest
from src.backtest.metrics import compute_metrics, save_report


def compute_extra(result) -> dict:
    r_mults = [t.r_multiple for t in result.trades]
    if not r_mults:
        return {"tail_ratio": 0.0, "max_losing_streak": 0}
    arr   = np.array(r_mults)
    top5  = np.percentile(arr, 95)
    bot5  = abs(np.percentile(arr, 5))
    tail  = top5 / bot5 if bot5 > 0 else float("inf")
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

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    r2_p  = cfg["rsi2_strategy"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]

    print("Loading SPY data...")
    price_data = fetch_all(["SPY"], start, end, min_bars=220, refresh=args.refresh)
    spy_df = price_data["SPY"]

    signals = generate_rsi2_signals(spy_df, r2_p)
    print(f"  {len(signals)} RSI(2) signals generated")

    if not signals:
        print("⚠️  시그널 없음")
        return

    result = run_rsi2_backtest(signals, spy_df, cfg)
    print(f"  {len(result.trades)} trades closed")

    m     = compute_metrics(result)
    extra = compute_extra(result)
    save_report(m, result, output_dir="backtest_results", prefix="rsi2_spy")

    print("\n── Connors RSI(2) SPY Results ────────────────────────")
    for k, v in m.items():
        if k != "exit_reasons":
            print(f"  {k:30s}: {v}")
    print(f"  {'tail_ratio':30s}: {extra['tail_ratio']}")
    print(f"  {'max_losing_streak':30s}: {extra['max_losing_streak']}")

    print("\n청산 사유:")
    for reason, cnt in m.get("exit_reasons", {}).items():
        print(f"  {reason:20s}: {cnt}")

    print("\n게이트 체크 [Type 1 — Mean Reversion]:")
    gates = {
        "Trades ≥ 50":          m["total_trades"]    >= 50,
        "Win rate ≥ 70%":       m["win_rate"]         >= 0.70,
        "Profit Factor ≥ 1.8":  m["profit_factor"]    >= 1.8,
        "MDD ≥ -12%":           m["max_drawdown_pct"] >= -12,
        "Sharpe ≥ 1.0":         m["sharpe"]           >= 1.0,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")
    if all(gates.values()):
        print("\n✅ 전 게이트 통과")
    else:
        print(f"\n❌ 미달: {', '.join(d for d, p in gates.items() if not p)}")


if __name__ == "__main__":
    main()
