"""
Bollinger + RSI Double 백테스트
"""
import argparse
import time
from pathlib import Path
import yaml

from src.fetch.prices import fetch_all
from src.backtest.bb_rsi_engine import run_bb_rsi_backtest
from src.backtest.qqq_225ma_engine import compute_metrics


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]
    sym   = cfg.get("bb_rsi_strategy", {}).get("symbol", "SPY")

    print(f"Loading {sym}...")
    t0 = time.time()
    data = fetch_all([sym], start, end, min_bars=210, refresh=args.refresh)
    print(f"  Done in {time.time()-t0:.1f}s")
    if sym not in data:
        return

    equity, trades = run_bb_rsi_backtest(data[sym], cfg)
    m = compute_metrics(equity, trades, cap)

    print(f"\n── BB+RSI Double {sym} Results ─────────────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    df = data[sym]
    bh = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    print(f"\n  {sym} Buy&Hold       : {bh:.2f}%")

    print("\n게이트 체크:")
    gates = {
        "CAGR ≥ 5%":      m["cagr_pct"]         >= 5.0,
        "MDD ≥ -15%":     m["max_drawdown_pct"]  >= -15,
        "Sharpe ≥ 0.7":   m["sharpe"]            >= 0.7,
        "PF ≥ 1.5":       m["profit_factor"]     >= 1.5,
        "WR ≥ 55%":       m["win_rate"]          >= 0.55,
    }
    for d, ok in gates.items():
        print(f"  {'✅' if ok else '❌'} {d}")
    if all(gates.values()):
        print("\n✅ 전 게이트 통과")


if __name__ == "__main__":
    main()
