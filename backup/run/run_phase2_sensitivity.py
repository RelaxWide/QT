"""
Phase 2 민감도 분석: stop_atr_mult × cloud_thickness_min_pct
사용법: python run_phase2_sensitivity.py [--tickers AAPL MSFT ...]
"""
import argparse
import time
from pathlib import Path
from itertools import product

import pandas as pd
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.ichimoku import ichimoku
from src.indicators.regime import compute_regime
from src.strategy.cloud_support import generate_cloud_signals
from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics


STOP_MULTS   = [0.5, 1.0, 1.5]
THICK_PCTS   = [1.5, 3.0, 5.0]


def build_tenkan_trail(price_data: dict, tenkan_period: int) -> dict:
    return {
        sym: ichimoku(df, tenkan_period=tenkan_period)["tenkan"]
        for sym, df in price_data.items()
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    p2    = cfg["phase2_cloud_support"]

    tickers = args.tickers or get_sp500_tickers()
    print(f"Loading price data ({len(tickers)} tickers)...")
    price_data = fetch_all(tickers, start, end, min_bars=300, refresh=args.refresh)
    print(f"  {len(price_data)} symbols loaded")

    regime = compute_regime(
        start, end,
        ma_short=cfg["regime_filter"]["spy_ma_short"],
        ma_long=cfg["regime_filter"]["spy_ma_long"],
        vix_threshold=cfg["regime_filter"]["vix_threshold"],
    )

    trail_data = build_tenkan_trail(price_data, p2["tenkan_period"])

    results = []
    combos = list(product(STOP_MULTS, THICK_PCTS))
    total = len(combos)

    for i, (stop_mult, thick_pct) in enumerate(combos, 1):
        p2_mod = dict(p2)
        p2_mod["stop_atr_mult_below_senkou_a"] = stop_mult
        p2_mod["cloud_thickness_min_pct"]      = thick_pct

        all_signals = []
        for sym, df in price_data.items():
            all_signals.extend(generate_cloud_signals(sym, df, p2_mod))
        all_signals.sort(key=lambda s: s.entry_date)

        if not all_signals:
            results.append({
                "stop_atr_mult": stop_mult, "thick_pct": thick_pct,
                "trades": 0, "win_rate": 0, "profit_factor": 0,
                "total_return_pct": 0, "max_drawdown_pct": 0,
                "sharpe": 0, "avg_r": 0,
            })
            continue

        result  = run_backtest(all_signals, price_data, regime, cfg, trail_data=trail_data)
        metrics = compute_metrics(result)

        g_trades = "✅" if metrics["total_trades"] >= 100  else "❌"
        g_wr     = "✅" if metrics["win_rate"]    >= 0.33  else "❌"
        g_pf     = "✅" if metrics["profit_factor"] >= 1.5 else "❌"
        g_mdd    = "✅" if metrics["max_drawdown_pct"] >= -15 else "❌"
        g_sharpe = "✅" if metrics["sharpe"]      >= 0.8   else "❌"
        gates    = f"{g_trades}{g_wr}{g_pf}{g_mdd}{g_sharpe}"

        results.append({
            "stop_atr_mult": stop_mult,
            "thick_pct":     thick_pct,
            "trades":        metrics["total_trades"],
            "win_rate":      metrics["win_rate"],
            "avg_r":         metrics["avg_r"],
            "profit_factor": metrics["profit_factor"],
            "total_return_pct": metrics["total_return_pct"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "sharpe":        metrics["sharpe"],
            "gates":         gates,
        })

        print(f"[{i}/{total}] stop={stop_mult} thick={thick_pct}%  "
              f"T={metrics['total_trades']:3d}  WR={metrics['win_rate']:.1%}  "
              f"PF={metrics['profit_factor']:.2f}  Sharpe={metrics['sharpe']:.3f}  "
              f"MDD={metrics['max_drawdown_pct']:.1f}%  {gates}")

    df = pd.DataFrame(results).sort_values("sharpe", ascending=False)
    out = Path("backtest_results/phase2_sensitivity.csv")
    df.to_csv(out, index=False)

    print(f"\n{'stop':>6} {'thick':>6} {'T':>5} {'WR':>6} {'avgR':>7} {'PF':>6} {'ret%':>7} {'MDD%':>7} {'sharpe':>7}  gates")
    print("─" * 90)
    for _, row in df.iterrows():
        print(f"  {row['stop_atr_mult']:>4.1f}   {row['thick_pct']:>4.1f}  "
              f"{int(row['trades']):>5}  {row['win_rate']:>5.1%}  "
              f"{row['avg_r']:>6.3f}  {row['profit_factor']:>5.2f}  "
              f"{row['total_return_pct']:>6.1f}%  {row['max_drawdown_pct']:>6.1f}%  "
              f"{row['sharpe']:>6.3f}  {row['gates']}")

    print(f"\nResults saved: {out}")

    best = df.iloc[0]
    print(f"\n최적 조합: stop_atr_mult={best['stop_atr_mult']}, thick_pct={best['thick_pct']}%")
    all_pass = df[
        (df["trades"] >= 100) &
        (df["win_rate"] >= 0.33) &
        (df["profit_factor"] >= 1.5) &
        (df["max_drawdown_pct"] >= -15) &
        (df["sharpe"] >= 0.8)
    ]
    if not all_pass.empty:
        print(f"✅ 게이트 전부 통과한 조합 {len(all_pass)}개 발견!")
    else:
        print("❌ 게이트 전부 통과한 조합 없음 — 결과 분석 후 조건 재검토 필요")


if __name__ == "__main__":
    main()
