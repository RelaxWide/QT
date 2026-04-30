"""
225-Day MA QQQ Strategy 백테스트
"""
import argparse
import time
from pathlib import Path
import yaml

from src.fetch.prices import fetch_all
from src.backtest.qqq_225ma_engine import run_225ma_backtest, compute_metrics


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]
    sym   = cfg.get("qqq_225ma_strategy", {}).get("symbol", "QQQ")

    print(f"Loading {sym}...")
    t0 = time.time()
    pd_data = fetch_all([sym], start, end, min_bars=240, refresh=args.refresh)
    print(f"  Done in {time.time()-t0:.1f}s")

    if sym not in pd_data:
        print(f"❌ {sym} 데이터 없음")
        return

    equity, trades = run_225ma_backtest(pd_data[sym], cfg)
    m = compute_metrics(equity, trades, cap)

    print(f"\n── 225-Day MA {sym} Results ──────────────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    # buy & hold 비교
    df = pd_data[sym]
    bh = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    print(f"\n  {sym} Buy&Hold       : {bh:.2f}%")

    print("\n게이트 체크:")
    gates = {
        "CAGR ≥ 10%":     m["cagr_pct"]         >= 10.0,
        "MDD ≥ -25%":     m["max_drawdown_pct"]  >= -25,
        "Sharpe ≥ 0.8":   m["sharpe"]            >= 0.8,
        f"CAGR > {sym} B&H CAGR": m["cagr_pct"] >= ((df["close"].iloc[-1] / df["close"].iloc[0]) ** (252/len(df)) - 1) * 100,
    }
    for d, ok in gates.items():
        print(f"  {'✅' if ok else '❌'} {d}")
    if all(gates.values()):
        print("\n✅ 전 게이트 통과")


if __name__ == "__main__":
    main()
