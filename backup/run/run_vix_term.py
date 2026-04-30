"""
VIX Term Structure SPY Timing 백테스트
"""
import argparse
import time
from pathlib import Path
import yaml

from src.fetch.prices import fetch_all
from src.backtest.vix_term_engine import run_vix_term_backtest
from src.backtest.qqq_225ma_engine import compute_metrics


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]
    sym   = cfg.get("vix_term_strategy", {}).get("symbol", "SPY")

    print(f"Loading {sym}, ^VIX, ^VIX9D...")
    t0 = time.time()
    data = fetch_all([sym, "^VIX", "^VIX9D"], start, end, min_bars=30, refresh=args.refresh)
    print(f"  Done in {time.time()-t0:.1f}s ({len(data)} loaded)")

    if "^VIX9D" not in data:
        print("❌ ^VIX9D 데이터 없음 — 야후 파이낸스에서 미제공일 수 있음")
        return

    equity, trades = run_vix_term_backtest(data, cfg)
    m = compute_metrics(equity, trades, cap)

    print("\n── VIX Term Structure Results ────────────────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    if sym in data:
        spy = data[sym]["close"]
        n_yr = len(spy) / 252
        bh_cagr = ((spy.iloc[-1] / spy.iloc[0]) ** (1/n_yr) - 1) * 100
        print(f"\n  {sym} B&H CAGR       : {bh_cagr:.2f}%")

    print("\n게이트 체크:")
    gates = {
        "CAGR ≥ 7%":      m["cagr_pct"]         >= 7.0,
        "MDD ≥ -15%":     m["max_drawdown_pct"]  >= -15,
        "Sharpe ≥ 1.0":   m["sharpe"]            >= 1.0,
        "PF ≥ 1.5":       m["profit_factor"]     >= 1.5,
    }
    for d, ok in gates.items():
        print(f"  {'✅' if ok else '❌'} {d}")
    if all(gates.values()):
        print("\n✅ 전 게이트 통과")


if __name__ == "__main__":
    main()
