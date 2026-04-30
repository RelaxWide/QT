"""
Minervini VCP 백테스트
사용법: python run_vcp.py [--tickers AAPL ...] [--refresh]

매일 S&P500 전체 스캔으로 VCP 패턴 검출 → 수십 분 소요 가능.
"""
import argparse
import time
from pathlib import Path

import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.backtest.vcp_engine import run_vcp_backtest, compute_vcp_metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]
    vcp_p = cfg.get("vcp_strategy", {})

    tickers = args.tickers or get_sp500_tickers()
    if "SPY" not in tickers:
        tickers = ["SPY"] + list(tickers)

    min_bars = vcp_p.get("ma200_period", 200) + 60
    print(f"Loading price data ({len(tickers)} tickers)...")
    t0 = time.time()
    price_data = fetch_all(tickers, start, end, min_bars=min_bars, refresh=args.refresh)
    print(f"  {len(price_data)} symbols loaded in {time.time()-t0:.1f}s")

    if "SPY" not in price_data:
        print("❌ SPY 데이터 없음")
        return

    print("Running VCP (Minervini) backtest...")
    t0 = time.time()
    equity, trades = run_vcp_backtest(price_data, cfg)
    print(f"  Done in {time.time()-t0:.1f}s")

    m = compute_vcp_metrics(equity, trades, cap)

    print("\n── Minervini VCP Results ────────────────────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    spy_c    = price_data["SPY"]["close"]
    spy_ret  = (spy_c.iloc[-1] / spy_c.iloc[0] - 1) * 100
    n_years  = len(equity) / 252
    spy_cagr = ((spy_c.iloc[-1] / spy_c.iloc[0]) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
    print(f"\nSPY Buy&Hold 비교:")
    print(f"  SPY 총 수익률: {spy_ret:.2f}%  |  CAGR: {spy_cagr:.2f}%")
    print(f"  전략 총 수익률: {m['total_return_pct']:.2f}%  |  CAGR: {m['cagr_pct']:.2f}%")

    print("\n게이트 체크 [Type 3 — Trend/Breakout]:")
    gates = {
        "CAGR ≥ 12%":      m["cagr_pct"]         >= 12.0,
        "MDD ≥ -22%":      m["max_drawdown_pct"]  >= -22,
        "Sharpe ≥ 0.85":   m["sharpe"]            >= 0.85,
        "WR ≥ 45%":        m["win_rate"]          >= 0.45,
        "PF ≥ 1.6":        m["profit_factor"]     >= 1.6,
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
