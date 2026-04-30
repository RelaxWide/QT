"""
Accelerating Dual Momentum 백테스트
"""
import argparse
import time
from pathlib import Path
import yaml

from src.fetch.prices import fetch_all
from src.backtest.adm_engine import run_adm_backtest
from src.backtest.qqq_225ma_engine import compute_metrics


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]
    s_cfg = cfg.get("adm_strategy", {})

    universe = list(s_cfg.get("offensive", ["SPY", "SCZ"])) + [s_cfg.get("defensive", "BND")]

    print(f"Loading {universe}...")
    t0 = time.time()
    data = fetch_all(universe, start, end, min_bars=140, refresh=args.refresh)
    print(f"  Done in {time.time()-t0:.1f}s ({len(data)} loaded)")

    equity, trades = run_adm_backtest(data, cfg)
    m = compute_metrics(equity, trades, cap)

    print("\n── ADM Results ──────────────────────────────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    if "SPY" in data:
        spy = data["SPY"]["close"]
        bh = (spy.iloc[-1] / spy.iloc[0] - 1) * 100
        n_yr = len(spy) / 252
        bh_cagr = ((spy.iloc[-1] / spy.iloc[0]) ** (1/n_yr) - 1) * 100
        print(f"\n  SPY Buy&Hold       : {bh:.2f}% (CAGR {bh_cagr:.2f}%)")

    print("\n게이트 체크:")
    gates = {
        "CAGR ≥ 8%":      m["cagr_pct"]         >= 8.0,
        "MDD ≥ -20%":     m["max_drawdown_pct"]  >= -20,
        "Sharpe ≥ 0.8":   m["sharpe"]            >= 0.8,
        "PF ≥ 1.5":       m["profit_factor"]     >= 1.5,
    }
    for d, ok in gates.items():
        print(f"  {'✅' if ok else '❌'} {d}")
    if all(gates.values()):
        print("\n✅ 전 게이트 통과")


if __name__ == "__main__":
    main()
