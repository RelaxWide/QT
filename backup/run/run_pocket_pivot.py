"""
Pocket Pivot 백테스트
사용법: python run_pocket_pivot.py [--refresh]
"""
import argparse
import time
from pathlib import Path

import numpy as np
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.strategy.pocket_pivot import generate_pocket_pivot_signals
from src.backtest.swing_engine import run_swing_backtest
from src.backtest.qqq_225ma_engine import compute_metrics


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    s_cfg = cfg.get("pocket_pivot_strategy", {})
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]

    tickers = get_sp500_tickers()
    if "SPY" not in tickers:
        tickers = ["SPY"] + tickers

    print(f"Loading {len(tickers)} tickers...")
    t0 = time.time()
    data = fetch_all(tickers, start, end, min_bars=260, refresh=args.refresh)
    print(f"  {len(data)} loaded in {time.time()-t0:.1f}s")

    spy_close = data["SPY"]["close"]

    print("Generating Pocket Pivot signals...")
    t0 = time.time()
    signals = []
    for sym, df in data.items():
        if sym == "SPY":
            continue
        signals.extend(generate_pocket_pivot_signals(sym, df, s_cfg))
    signals.sort(key=lambda s: s.entry_date)
    print(f"  {len(signals)} signals in {time.time()-t0:.1f}s")

    if not signals:
        print("⚠️  시그널 없음")
        return

    print("Running backtest...")
    t0 = time.time()
    equity, trades = run_swing_backtest(signals, data, spy_close, cfg, "pocket_pivot_strategy")
    print(f"  Done in {time.time()-t0:.1f}s | {len(trades)} trades")

    m = compute_metrics(equity, trades, cap)

    print("\n── Pocket Pivot Results ─────────────────────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    n_yr = len(equity) / 252
    spy = data["SPY"]["close"]
    bh_cagr = ((spy.iloc[-1] / spy.iloc[0]) ** (1/n_yr) - 1) * 100
    print(f"\n  SPY B&H CAGR       : {bh_cagr:.2f}%")

    # 추가 통계
    if trades:
        pnls = [t["pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        avg_win = sum(wins)/len(wins) if wins else 0
        avg_loss = abs(sum(losses)/len(losses)) if losses else 0
        streak = max_streak = cur = 0
        for t in trades:
            if t["pnl"] <= 0:
                cur += 1; max_streak = max(max_streak, cur)
            else:
                cur = 0
        print(f"  {'avg_win_usd':30s}: {avg_win:.0f}")
        print(f"  {'avg_loss_usd':30s}: {avg_loss:.0f}")
        print(f"  {'max_losing_streak':30s}: {max_streak}")

    print("\n게이트 체크 [Type 2 — Trend Breakout]:")
    gates = {
        "CAGR ≥ 12%":    m["cagr_pct"]         >= 12.0,
        "MDD ≥ -20%":    m["max_drawdown_pct"]  >= -20,
        "Sharpe ≥ 0.85": m["sharpe"]            >= 0.85,
        "WR ≥ 45%":      m["win_rate"]          >= 0.45,
        "PF ≥ 1.5":      m["profit_factor"]     >= 1.5,
    }
    for d, ok in gates.items():
        print(f"  {'✅' if ok else '❌'} {d}")
    if all(gates.values()):
        print("\n✅ 전 게이트 통과")


if __name__ == "__main__":
    main()
