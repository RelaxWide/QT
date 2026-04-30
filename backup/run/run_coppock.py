"""
Coppock Curve SPY 백테스트
"""
import argparse
import time
from pathlib import Path
import yaml

from src.fetch.prices import fetch_all
from src.backtest.coppock_engine import run_coppock_backtest
from src.backtest.qqq_225ma_engine import compute_metrics


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]
    sym   = cfg.get("coppock_strategy", {}).get("symbol", "SPY")

    print(f"Loading {sym}...")
    t0 = time.time()
    data = fetch_all([sym], start, end, min_bars=300, refresh=args.refresh)
    print(f"  Done in {time.time()-t0:.1f}s")
    if sym not in data:
        return

    equity, trades = run_coppock_backtest(data[sym], cfg)
    m = compute_metrics(equity, trades, cap)

    print(f"\n── Coppock Curve {sym} Results ───────────────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    df = data[sym]
    bh = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    n_yr = len(df) / 252
    bh_cagr = ((df["close"].iloc[-1] / df["close"].iloc[0]) ** (1/n_yr) - 1) * 100
    print(f"\n  {sym} Buy&Hold       : {bh:.2f}% (CAGR {bh_cagr:.2f}%)")

    print("\n게이트 체크:")
    gates = {
        f"CAGR ≥ {sym} B&H CAGR":  m["cagr_pct"]         >= bh_cagr,
        "MDD ≥ -25%":              m["max_drawdown_pct"]  >= -25,
        "Sharpe ≥ 0.7":            m["sharpe"]            >= 0.7,
        "거래 ≤ 5/년":             m["n_trades"] / max(n_yr, 1) <= 5,
    }
    for d, ok in gates.items():
        print(f"  {'✅' if ok else '❌'} {d}")
    if all(gates.values()):
        print("\n✅ 전 게이트 통과")


if __name__ == "__main__":
    main()
