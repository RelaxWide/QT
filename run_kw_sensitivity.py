"""
KW 파라미터 sensitivity 그리드 분석 (Super Value / Super Quality 공통).

small_cap_pct × top_n 조합별 12년 백테스트 → CAGR/MDD/Sharpe/alpha 비교.

사용:
  python run_kw_sensitivity.py --strategy super_value
  python run_kw_sensitivity.py --strategy super_quality
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.fetch.universe import get_kospi_all_tickers
from src.fetch.prices import fetch_all
from src.fetch.fundamentals_kr import build_fundamentals_panel
from src.backtest.quarterly_engine import run_quarterly_backtest, compute_quarterly_metrics
from src.markets import get_profile
from src.strategy._kw_common import rebalance_dates_kr_quarterly, adjust_signals_to_trading


SMALL_CAP_GRID = [0.10, 0.15, 0.20, 0.30, 0.50]
TOP_N_GRID     = [10, 15, 20, 30]


def _gen_signals(strategy, panel, price_data, params, start, end):
    if strategy == "super_value":
        from src.strategy.kw_super_value import generate_super_value_signals
        return generate_super_value_signals(panel, price_data, params, start, end)
    elif strategy == "super_quality":
        from src.strategy.kw_super_quality import generate_super_quality_signals
        return generate_super_quality_signals(panel, price_data, params, start, end)
    else:
        from src.strategy.kw_ultra import generate_ultra_signals
        return generate_ultra_signals(panel, price_data, params, start, end)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=["super_value","super_quality","ultra"],
                        default="super_value")
    args = parser.parse_args()
    strategy = args.strategy
    profile = get_profile("kr")
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["market"] = {"code":profile.code,"regime_index":profile.index_ticker,"currency":profile.currency}
    cfg["backtest"]["initial_capital_usd"] = cfg["backtest"].get("initial_capital_krw", 50_000_000)
    cap = cfg["backtest"]["initial_capital_usd"]
    base_params = dict(cfg[f"kw_{strategy}"])
    print(f"[KW {strategy} sensitivity]")

    start = "2014-01-01"
    end   = pd.Timestamp.today().strftime("%Y-%m-%d")

    print("Loading data once (price + fundamentals)...")
    t0 = time.time()
    tickers = [profile.index_ticker] + get_kospi_all_tickers()
    price_data = fetch_all(tickers, start, end, min_bars=120, market="kr")
    print(f"  Price: {len(price_data)} loaded in {time.time()-t0:.1f}s")

    rebal_months = base_params["rebalance_months"]
    rebal_dom    = base_params["rebalance_dom"]
    raw_rebal = rebalance_dates_kr_quarterly(start, end, rebal_months, rebal_dom)
    calendar  = price_data[profile.index_ticker].index
    rebal_dates = adjust_signals_to_trading(raw_rebal, calendar)
    t0 = time.time()
    panel = build_fundamentals_panel(rebal_dates)
    print(f"  Fundamentals: {len(panel)} tickers in {time.time()-t0:.1f}s")

    # KOSPI B&H
    bench_c = price_data[profile.index_ticker]["close"]
    n_years = len(bench_c) / 252
    bench_cagr = ((bench_c.iloc[-1] / bench_c.iloc[0]) ** (1/n_years) - 1) * 100 if n_years > 0 else 0

    rows = []
    print(f"\nGrid: {len(SMALL_CAP_GRID)} × {len(TOP_N_GRID)} = {len(SMALL_CAP_GRID)*len(TOP_N_GRID)} runs")
    for scp in SMALL_CAP_GRID:
        for tn in TOP_N_GRID:
            params = dict(base_params)
            params["small_cap_pct"] = scp
            params["top_n"] = tn
            t0 = time.time()
            sigs = _gen_signals(strategy, panel, price_data, params, start, end)
            eq, _ = run_quarterly_backtest(sigs, price_data, cfg, market="kr")
            m = compute_quarterly_metrics(eq, cap)
            alpha = m["cagr_pct"] - bench_cagr
            row = {
                "small_cap_pct": scp, "top_n": tn,
                "n_signals":     len(sigs),
                "cagr":          m["cagr_pct"],
                "mdd":           m["max_drawdown_pct"],
                "sharpe":        m["sharpe"],
                "calmar":        m["calmar"],
                "monthly_wr":    m["monthly_win_rate"],
                "alpha":         round(alpha, 2),
                "elapsed":       round(time.time()-t0, 1),
            }
            rows.append(row)
            print(f"  scp={scp:.2f} top={tn:2d}: CAGR {m['cagr_pct']:+6.2f}%  MDD {m['max_drawdown_pct']:+6.2f}%  "
                  f"Sharpe {m['sharpe']:.2f}  alpha {alpha:+5.2f}%p  ({row['elapsed']:.1f}s)")

    df = pd.DataFrame(rows)
    out_dir = Path("backtest_results/kr")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"kw_{strategy}_sensitivity.csv"
    df.to_csv(out_file, index=False)
    print(f"\nSaved: {out_file}")

    print("\n=== 상위 5 (Sharpe) ===")
    print(df.sort_values("sharpe", ascending=False).head().to_string(index=False))
    print("\n=== 상위 5 (CAGR) ===")
    print(df.sort_values("cagr", ascending=False).head().to_string(index=False))
    print("\n=== 상위 5 (Calmar, MDD 대비 CAGR) ===")
    print(df.sort_values("calmar", ascending=False).head().to_string(index=False))


if __name__ == "__main__":
    main()
