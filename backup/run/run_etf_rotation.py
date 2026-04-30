"""
Monthly Momentum ETF Rotation 백테스트 (SPY/EEM/TLT)
사용법: python run_etf_rotation.py [--refresh]
"""
import argparse
from pathlib import Path

import yaml

from src.fetch.prices import fetch_all
from src.strategy.etf_rotation import generate_rotation_signals
from src.backtest.etf_rotation_engine import run_etf_rotation_backtest, compute_rotation_metrics

TICKERS  = ["SPY", "EEM", "TLT"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]

    print(f"Loading price data: {TICKERS}...")
    price_data = fetch_all(TICKERS, start, end, min_bars=30, refresh=args.refresh)
    print(f"  {len(price_data)} symbols loaded")

    print("Generating rotation signals...")
    signals = generate_rotation_signals(price_data, TICKERS)
    print(f"  {len(signals)} monthly signals")

    if not signals:
        print("⚠️  시그널 없음")
        return

    # 선택 통계 출력
    from collections import Counter
    picks = Counter(s.top_asset for s in signals)
    print("  Asset selection counts:")
    for sym, cnt in picks.most_common():
        print(f"    {sym}: {cnt}회 ({cnt/len(signals)*100:.1f}%)")

    print("Running ETF Rotation backtest...")
    equity = run_etf_rotation_backtest(signals, price_data, cfg)
    m      = compute_rotation_metrics(equity, cap)

    print("\n── Monthly ETF Rotation Results ──────────────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    # Type 2 (자산 로테이션) 게이트
    print("\n게이트 체크 [Type 2 — Asset Rotation]:")
    gates = {
        "CAGR ≥ 7%":        m["cagr_pct"]           >= 7.0,
        "MDD ≥ -20%":       m["max_drawdown_pct"]    >= -20,
        "Sharpe ≥ 0.7":     m["sharpe"]              >= 0.7,
        "Monthly WR ≥ 55%": m["monthly_win_rate"]    >= 0.55,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")

    if all(gates.values()):
        print("\n✅ 전 게이트 통과")
    else:
        failed = [d for d, p in gates.items() if not p]
        print(f"\n❌ 미달 항목: {', '.join(failed)}")

    # SPY 단순 보유 vs 전략 비교
    spy_eq = price_data["SPY"]["close"] / price_data["SPY"]["close"].iloc[0] * cap
    spy_ret = (spy_eq.iloc[-1] - cap) / cap * 100
    n_years  = len(equity) / 252
    spy_cagr = ((spy_eq.iloc[-1] / cap) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
    print(f"\nSPY Buy&Hold 비교:")
    print(f"  SPY 총 수익률: {spy_ret:.2f}%  |  CAGR: {spy_cagr:.2f}%")
    print(f"  전략 총 수익률: {m['total_return_pct']:.2f}%  |  CAGR: {m['cagr_pct']:.2f}%")


if __name__ == "__main__":
    main()
