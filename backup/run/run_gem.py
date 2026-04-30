"""
Antonacci Global Equity Momentum (GEM) 백테스트
사용법: python run_gem.py [--refresh]
"""
import argparse
from pathlib import Path
import yaml

from src.fetch.prices import fetch_all
from src.strategy.gem_dual_momentum import generate_gem_signals
from src.backtest.etf_rotation_engine import run_etf_rotation_backtest, compute_rotation_metrics


def run_gem_backtest(signals, price_data, cfg):
    """GEM은 단일 자산 보유 → etf_rotation_engine 재활용."""
    from src.strategy.etf_rotation import RotationSignal
    rot_signals = [
        RotationSignal(date=s.date, top_asset=s.hold, returns={})
        for s in signals
    ]
    return run_etf_rotation_backtest(rot_signals, price_data, cfg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg    = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    g_p    = cfg["gem_strategy"]
    start  = cfg["data"]["start_date"]
    end    = cfg["data"]["end_date"]
    cap    = cfg["backtest"]["initial_capital_usd"]

    tickers = ["SPY", "EFA", g_p["bond_proxy"]]
    print(f"Loading: {tickers}...")
    price_data = fetch_all(tickers, start, end, min_bars=15, refresh=args.refresh)
    print(f"  {len(price_data)} symbols loaded")

    signals = generate_gem_signals(price_data, g_p["lookback_months"], g_p["bond_proxy"])
    print(f"  {len(signals)} monthly signals")

    from collections import Counter
    picks = Counter(s.hold for s in signals)
    print("  Holdings:")
    for sym, cnt in picks.most_common():
        print(f"    {sym}: {cnt}회 ({cnt/len(signals)*100:.1f}%)")

    equity = run_gem_backtest(signals, price_data, cfg)
    m      = compute_rotation_metrics(equity, cap)

    print("\n── GEM Dual Momentum Results ─────────────────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    spy_c = price_data["SPY"]["close"]
    n_years = len(equity) / 252
    spy_cagr = ((spy_c.iloc[-1] / spy_c.iloc[0]) ** (1 / n_years) - 1) * 100
    print(f"\nSPY CAGR: {spy_cagr:.2f}%  vs  전략 CAGR: {m['cagr_pct']:.2f}%")

    print("\n게이트 체크 [Type 2 — Rotation]:")
    gates = {
        "CAGR ≥ 7%":        m["cagr_pct"]         >= 7.0,
        "MDD ≥ -20%":       m["max_drawdown_pct"]  >= -20,
        "Sharpe ≥ 0.7":     m["sharpe"]            >= 0.7,
        "Monthly WR ≥ 55%": m["monthly_win_rate"]  >= 0.55,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")
    if all(gates.values()):
        print("\n✅ 전 게이트 통과")
    else:
        print(f"\n❌ 미달: {', '.join(d for d, p in gates.items() if not p)}")


if __name__ == "__main__":
    main()
