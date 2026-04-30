"""
Connors TPS (Time/Price Scale-In) ETF 백테스트
사용법: python run_tps.py [--refresh]
"""
import argparse
import time
from pathlib import Path

import yaml

from src.fetch.prices import fetch_all
from src.backtest.tps_engine import run_tps_backtest, compute_tps_metrics
from src.strategy.connors_tps import DEFAULT_UNIVERSE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]

    universe = cfg.get("tps_strategy", {}).get("universe", DEFAULT_UNIVERSE)

    print(f"Loading ETF data ({len(universe)} tickers)...")
    t0 = time.time()
    price_data = fetch_all(universe, start, end, min_bars=210, refresh=args.refresh)
    print(f"  {len(price_data)} ETFs loaded in {time.time()-t0:.1f}s")

    print("Running Connors TPS backtest...")
    t0 = time.time()
    equity, trades = run_tps_backtest(price_data, cfg)
    print(f"  Done in {time.time()-t0:.1f}s")

    m = compute_tps_metrics(equity, trades, cap)

    print("\n── Connors TPS (ETF Mean Reversion) Results ─────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    if "SPY" in price_data:
        spy_c    = price_data["SPY"]["close"]
        spy_ret  = (spy_c.iloc[-1] / spy_c.iloc[0] - 1) * 100
        n_years  = len(equity) / 252
        spy_cagr = ((spy_c.iloc[-1] / spy_c.iloc[0]) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
        print(f"\nSPY Buy&Hold 비교:")
        print(f"  SPY 총 수익률: {spy_ret:.2f}%  |  CAGR: {spy_cagr:.2f}%")
        print(f"  전략 총 수익률: {m['total_return_pct']:.2f}%  |  CAGR: {m['cagr_pct']:.2f}%")

    print("\n게이트 체크 [Type 4 — Mean Reversion]:")
    gates = {
        "CAGR ≥ 8%":      m["cagr_pct"]         >= 8.0,
        "MDD ≥ -12%":     m["max_drawdown_pct"]  >= -12,
        "Sharpe ≥ 1.0":   m["sharpe"]            >= 1.0,
        "WR ≥ 70%":       m["win_rate"]          >= 0.70,
        "PF ≥ 2.5":       m["profit_factor"]     >= 2.5,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")

    if all(gates.values()):
        print("\n✅ 전 게이트 통과 — paper trading 통합 가능")
    else:
        failed = [d for d, p in gates.items() if not p]
        print(f"\n❌ 미달: {', '.join(failed)}")


if __name__ == "__main__":
    main()
