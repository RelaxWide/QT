"""
Clenow Stocks on the Move 백테스트
사용법: python run_clenow.py [--tickers AAPL MSFT ...] [--refresh]

S&P500 전체 실행 시 스코어 계산에 수 분 소요.
"""
import argparse
import time
from pathlib import Path

import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.backtest.clenow_engine import run_clenow_backtest, compute_clenow_metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]
    cl_p  = cfg["clenow_strategy"]

    # 티커 목록
    tickers = args.tickers or get_sp500_tickers()
    if "SPY" not in tickers:
        tickers = ["SPY"] + list(tickers)

    min_bars = cl_p["reg_lookback"] + cl_p["ma100_period"] + 10
    print(f"Loading price data ({len(tickers)} tickers)...")
    t0 = time.time()
    price_data = fetch_all(tickers, start, end, min_bars=min_bars, refresh=args.refresh)
    print(f"  {len(price_data)} symbols loaded in {time.time()-t0:.1f}s")

    if "SPY" not in price_data:
        print("❌ SPY 데이터 없음")
        return

    print("Running Clenow backtest (weekly rebalancing)...")
    t0 = time.time()
    equity, rebal_log = run_clenow_backtest(price_data, cfg)
    print(f"  Done in {time.time()-t0:.1f}s")

    m = compute_clenow_metrics(equity, cap)

    # equity curve 저장 (포트폴리오 합성 분석용)
    out_dir = Path("backtest_results")
    out_dir.mkdir(parents=True, exist_ok=True)
    equity.to_frame("equity").to_csv(out_dir / "clenow_equity.csv", index_label="date")

    print("\n── Clenow Stocks on the Move Results ────────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    if rebal_log:
        avg_held = sum(r["n_held"] for r in rebal_log) / len(rebal_log)
        regime_pct = sum(1 for r in rebal_log if r["regime"]) / len(rebal_log) * 100
        print(f"  {'avg_positions_held':30s}: {avg_held:.1f}")
        print(f"  {'regime_in_pct':30s}: {regime_pct:.1f}%")

    # SPY 비교
    spy_c = price_data["SPY"]["close"]
    spy_ret = (spy_c.iloc[-1] / spy_c.iloc[0] - 1) * 100
    n_years  = len(equity) / 252
    spy_cagr = ((spy_c.iloc[-1] / spy_c.iloc[0]) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
    print(f"\nSPY Buy&Hold 비교:")
    print(f"  SPY 총 수익률: {spy_ret:.2f}%  |  CAGR: {spy_cagr:.2f}%")
    print(f"  전략 총 수익률: {m['total_return_pct']:.2f}%  |  CAGR: {m['cagr_pct']:.2f}%")

    # 게이트 체크 (Type 3 추세)
    print("\n게이트 체크 [Type 3 — Trend/Momentum]:")
    gates = {
        "CAGR ≥ 10%":        m["cagr_pct"]         >= 10.0,
        "MDD ≥ -25%":        m["max_drawdown_pct"]  >= -25,
        "Sharpe ≥ 0.7":      m["sharpe"]            >= 0.7,
        "Monthly WR ≥ 55%":  m["monthly_win_rate"]  >= 0.55,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")

    if all(gates.values()):
        print("\n✅ 전 게이트 통과 — Phase 4와 포트폴리오 합성 진행 가능")
    else:
        failed = [d for d, p in gates.items() if not p]
        print(f"\n❌ 미달 항목: {', '.join(failed)}")


if __name__ == "__main__":
    main()
