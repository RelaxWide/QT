"""
Connors RSI 복합 mean reversion 백테스트
사용법: python run_connors_rsi.py [--tickers ...] [--refresh]
"""
import argparse
import time
from pathlib import Path

import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.backtest.connors_rsi_engine import (
    run_connors_rsi_backtest,
    compute_connors_metrics,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]

    tickers = args.tickers or get_sp500_tickers()
    if "SPY" not in tickers:
        tickers = ["SPY"] + list(tickers)

    print(f"Loading price data ({len(tickers)} tickers)...")
    t0 = time.time()
    price_data = fetch_all(tickers, start, end, min_bars=210, refresh=args.refresh)
    print(f"  {len(price_data)} symbols loaded in {time.time()-t0:.1f}s")

    if "SPY" not in price_data:
        print("❌ SPY 데이터 없음")
        return

    print("Running Connors RSI backtest...")
    t0 = time.time()
    equity, trades = run_connors_rsi_backtest(price_data, cfg)
    print(f"  Done in {time.time()-t0:.1f}s")

    m = compute_connors_metrics(equity, trades, cap)

    print("\n── Connors RSI Mean Reversion Results ───────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    spy_c    = price_data["SPY"]["close"]
    spy_ret  = (spy_c.iloc[-1] / spy_c.iloc[0] - 1) * 100
    n_years  = len(equity) / 252
    spy_cagr = ((spy_c.iloc[-1] / spy_c.iloc[0]) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
    print(f"\nSPY Buy&Hold 비교:")
    print(f"  SPY 총 수익률: {spy_ret:.2f}%  |  CAGR: {spy_cagr:.2f}%")
    print(f"  전략 총 수익률: {m['total_return_pct']:.2f}%  |  CAGR: {m['cagr_pct']:.2f}%")

    print("\n게이트 체크 [Type 4 — Mean Reversion]:")
    gates = {
        "WR ≥ 60%":          m["win_rate"]         >= 0.60,
        "PF ≥ 2.0":          m["profit_factor"]    >= 2.0,
        "MDD ≥ -15%":        m["max_drawdown_pct"]  >= -15,
        "Sharpe ≥ 0.9":      m["sharpe"]            >= 0.9,
        "평균보유일 ≤ 5일":  m["avg_hold_days"]     <= 5.0,
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
