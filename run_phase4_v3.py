"""
Phase 4-v3 backtest runner.

Usage examples:
  py -3.13 run_phase4_v3.py
  py -3.13 run_phase4_v3.py --max-cloud-gap-pct 12 --tag gap12
  py -3.13 run_phase4_v3.py --require-support-touch --targets 1.0 2.0 --trail-level top
"""
from __future__ import annotations

import argparse
import copy
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics, save_report
from src.fetch.prices import fetch_all
from src.fetch.universe import get_sp500_tickers
from src.indicators.factors import build_factor_matrices
from src.indicators.regime import compute_regime
from src.strategy.factor_stack_v3 import build_cloud_trails, generate_factor_signals_v3


def _build_fip_rank(price_data: dict, period: int = 60) -> pd.DataFrame:
    """Build Frog-in-the-Pan Information Discreteness rank matrix.

    Higher rank = smoother (more continuous) momentum = preferred by FIP theory.
    ID = sign(return_Nd) * (pct_down_days - pct_up_days)
    ID < 0 → continuous upward momentum (good). We rank by -ID so higher = better.
    """
    close_mat = pd.DataFrame(
        {sym: df["close"] for sym, df in price_data.items()}
    ).sort_index()
    daily_ret = close_mat.pct_change()
    pct_up = (daily_ret > 0).rolling(period).mean()
    pct_down = (daily_ret < 0).rolling(period).mean()
    ret_nd = close_mat.pct_change(period)
    id_mat = np.sign(ret_nd) * (pct_down - pct_up)
    return (-id_mat).rank(axis=1, pct=True, na_option="keep")


def _print_metrics(metrics: dict) -> None:
    for key, value in metrics.items():
        if key != "exit_reasons":
            print(f"  {key:30s}: {value}")
    print("\n  Exit reasons:")
    for reason, count in metrics.get("exit_reasons", {}).items():
        print(f"    {reason:30s}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--tag", default=None)
    parser.add_argument("--max-cloud-gap-pct", type=float, default=None)
    parser.add_argument("--min-cloud-thickness-pct", type=float, default=None)
    parser.add_argument("--require-support-touch", action="store_true")
    parser.add_argument("--require-bullish-reclaim", action="store_true")
    parser.add_argument("--require-tenkan-reclaim", action="store_true")
    parser.add_argument("--support-touch-tolerance", type=float, default=None)
    parser.add_argument("--targets", nargs="*", type=float)
    parser.add_argument("--weights", nargs="*", type=float)
    parser.add_argument("--trail-level", choices=["donchian", "top", "mid", "bottom"], default=None)
    parser.add_argument("--use-trend-template", action="store_true")
    parser.add_argument("--trend-high52-min-ratio", type=float, default=None)
    parser.add_argument("--trend-low52-min-ratio", type=float, default=None)
    parser.add_argument("--breakout-volume-mult", type=float, default=None)
    parser.add_argument("--pullback-volume-max-ratio", type=float, default=None)
    parser.add_argument("--max-recent-atr-to-long-atr-ratio", type=float, default=None)
    parser.add_argument("--use-52w-high-rank", action="store_true")
    parser.add_argument("--high52-rank-top-pct", type=float, default=None)
    parser.add_argument("--require-pocket-pivot", action="store_true")
    parser.add_argument("--pocket-pivot-lookback", type=int, default=None)
    parser.add_argument("--use-momentum", action="store_true")
    parser.add_argument("--momentum-top-pct", type=float, default=None)
    parser.add_argument("--min-factors-required", type=int, default=None)
    parser.add_argument("--atr-pct-max", type=float, default=None)
    parser.add_argument("--rsi-min", type=float, default=None)
    parser.add_argument("--rsi-max", type=float, default=None)
    parser.add_argument("--adx-min", type=float, default=None)
    # RS Line New High filter
    parser.add_argument("--use-rs-line-new-high", action="store_true")
    parser.add_argument("--rs-line-period", type=int, default=None)
    parser.add_argument("--rs-line-price-max-ratio", type=float, default=None,
                        help="Max stock price / 52w high ratio (e.g. 0.98, 1.00). Omit to disable.")
    parser.add_argument("--rs-line-near-high-ratio", type=float, default=None,
                        help="RS Line >= rolling_max * ratio (e.g. 0.99). Default 1.0 (exact).")
    parser.add_argument("--rs-line-recent-high-days", type=int, default=None,
                        help="RS Line was at/near high within last N days (e.g. 5, 10)")
    parser.add_argument("--rs-line-above-ma-period", type=int, default=None,
                        help="Mode A: RS Line must be above its N-day MA at signal bar")
    parser.add_argument("--rs-line-slope-positive-days", type=int, default=None,
                        help="Mode B: RS Line must be higher than N days ago at signal bar")
    parser.add_argument("--rs-line-check-at-breakout", action="store_true",
                        help="Mode C: check RS Line at the breakout bar, not the pullback bar")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Override config start_date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None,
                        help="Override config end_date (YYYY-MM-DD)")
    # ADR filter
    parser.add_argument("--adr-min", type=float, default=None,
                        help="Min avg daily range (H-L)/C, e.g. 0.02, 0.025, 0.03")
    parser.add_argument("--adr-period", type=int, default=None)
    # Calendar filter
    parser.add_argument("--skip-months", nargs="*", type=int,
                        help="Month numbers to block entries in, e.g. 9 for September")
    # FIP filter / score
    parser.add_argument("--use-fip-filter", action="store_true",
                        help="Hard filter: require smooth (continuous) momentum via FIP ID")
    parser.add_argument("--use-fip-score", action="store_true",
                        help="Blend FIP rank into signal score (30% weight)")
    parser.add_argument("--fip-period", type=int, default=None)
    parser.add_argument("--fip-min-rank", type=float, default=None,
                        help="Min FIP rank percentile for hard filter (default 0.5)")
    parser.add_argument("--fip-score-weight", type=float, default=None,
                        help="FIP weight in score blend (default 0.3)")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    p1 = cfg["phase1_breakout_pullback"]
    p2 = cfg["phase2_cloud_support"]
    p3 = cfg["phase3_hybrid"]
    p4 = cfg["phase4_factor_stack"]
    v3 = copy.deepcopy(cfg.get("phase4_v3", {}))
    start = args.start_date or cfg["data"]["start_date"]
    end = args.end_date or cfg["data"]["end_date"]
    cache_dir = cfg["data"].get("cache_dir", "data/raw")

    if args.max_cloud_gap_pct is not None:
        v3["max_cloud_gap_pct"] = args.max_cloud_gap_pct
    if args.min_cloud_thickness_pct is not None:
        v3["min_cloud_thickness_pct"] = args.min_cloud_thickness_pct
    if args.require_support_touch:
        v3["require_support_touch"] = True
    if args.require_bullish_reclaim:
        v3["require_bullish_reclaim"] = True
    if args.require_tenkan_reclaim:
        v3["require_tenkan_reclaim"] = True
    if args.support_touch_tolerance is not None:
        v3["support_touch_tolerance"] = args.support_touch_tolerance
    if args.targets:
        v3["partial_exit_r_multiples"] = args.targets
    if args.weights:
        v3["partial_exit_weights"] = args.weights
    if args.trail_level:
        v3["trail_level"] = args.trail_level
    if args.use_trend_template:
        v3["use_trend_template"] = True
    if args.trend_high52_min_ratio is not None:
        v3["trend_high52_min_ratio"] = args.trend_high52_min_ratio
    if args.trend_low52_min_ratio is not None:
        v3["trend_low52_min_ratio"] = args.trend_low52_min_ratio
    if args.breakout_volume_mult is not None:
        v3["breakout_volume_mult"] = args.breakout_volume_mult
    if args.pullback_volume_max_ratio is not None:
        v3["pullback_volume_max_ratio"] = args.pullback_volume_max_ratio
    if args.max_recent_atr_to_long_atr_ratio is not None:
        v3["max_recent_atr_to_long_atr_ratio"] = args.max_recent_atr_to_long_atr_ratio
    if args.use_52w_high_rank:
        v3["use_52w_high_rank"] = True
    if args.high52_rank_top_pct is not None:
        v3["high52_rank_top_pct"] = args.high52_rank_top_pct
    if args.require_pocket_pivot:
        v3["require_pocket_pivot"] = True
    if args.pocket_pivot_lookback is not None:
        v3["pocket_pivot_lookback"] = args.pocket_pivot_lookback
    if args.use_momentum:
        p4["use_momentum"] = True
    if args.momentum_top_pct is not None:
        p4["momentum_top_pct"] = args.momentum_top_pct
    if args.min_factors_required is not None:
        p4["min_factors_required"] = args.min_factors_required
    if args.atr_pct_max is not None:
        v3["atr_pct_max"] = args.atr_pct_max
    if args.rsi_min is not None:
        v3["rsi_min"] = args.rsi_min
    if args.rsi_max is not None:
        v3["rsi_max"] = args.rsi_max
    if args.adx_min is not None:
        v3["adx_min"] = args.adx_min
    if args.use_rs_line_new_high:
        v3["use_rs_line_new_high"] = True
    if args.rs_line_period is not None:
        v3["rs_line_period"] = args.rs_line_period
    if args.rs_line_price_max_ratio is not None:
        v3["rs_line_price_max_ratio"] = args.rs_line_price_max_ratio
    if args.rs_line_near_high_ratio is not None:
        v3["rs_line_near_high_ratio"] = args.rs_line_near_high_ratio
    if args.rs_line_recent_high_days is not None:
        v3["rs_line_recent_high_days"] = args.rs_line_recent_high_days
    if args.rs_line_above_ma_period is not None:
        v3["rs_line_above_ma_period"] = args.rs_line_above_ma_period
    if args.rs_line_slope_positive_days is not None:
        v3["rs_line_slope_positive_days"] = args.rs_line_slope_positive_days
    if args.rs_line_check_at_breakout:
        v3["rs_line_check_at_breakout"] = True
    if args.adr_min is not None:
        v3["adr_min"] = args.adr_min
    if args.adr_period is not None:
        v3["adr_period"] = args.adr_period
    if args.skip_months is not None:
        v3["skip_months"] = args.skip_months
    if args.use_fip_filter:
        v3["use_fip_filter"] = True
    if args.use_fip_score:
        v3["use_fip_score"] = True
    if args.fip_period is not None:
        v3["fip_period"] = args.fip_period
    if args.fip_min_rank is not None:
        v3["fip_min_rank"] = args.fip_min_rank
    if args.fip_score_weight is not None:
        v3["fip_score_weight"] = args.fip_score_weight

    p2_filter = dict(p2)
    p2_filter["cloud_filter_thickness_min_pct"] = p3["cloud_filter_thickness_min_pct"]
    p2_filter["cloud_filter_use_chikou"] = p3["cloud_filter_use_chikou"]
    p4["momentum_period"] = p4.get("momentum_period", 63)

    tickers = args.tickers or get_sp500_tickers()
    print(f"\nPhase 4-v3 | loading price data ({len(tickers)} tickers)")
    price_data = fetch_all(
        tickers,
        start,
        end,
        min_bars=300,
        refresh=args.refresh,
        cache_dir=cache_dir,
    )
    print(f"  {len(price_data)} symbols loaded")

    print("Computing regime...")
    regime = compute_regime(
        start,
        end,
        ma_short=cfg["regime_filter"]["spy_ma_short"],
        ma_long=cfg["regime_filter"]["spy_ma_long"],
        vix_threshold=cfg["regime_filter"]["vix_threshold"],
    )

    print("Building factor matrices...")
    t0 = time.time()
    mom_rank, bbw_rank, spy_mom = build_factor_matrices(
        price_data,
        mom_period=p4["momentum_period"],
        bb_period=p4["bbwidth_period"],
    )
    high52_rank = None
    if v3.get("use_52w_high_rank", False):
        high52_ratio = pd.DataFrame(
            {
                sym: df["close"] / df["high"].rolling(252).max()
                for sym, df in price_data.items()
            }
        )
        high52_rank = high52_ratio.rank(axis=1, pct=True)

    spx_series = None
    spy_df = price_data.get("SPY")
    if spy_df is None and v3.get("use_rs_line_new_high", False):
        print("  Fetching SPY for RS Line computation...")
        spy_data = fetch_all(["SPY"], start, end, min_bars=300, cache_dir=cache_dir)
        spy_df = spy_data.get("SPY")
    if spy_df is not None:
        spx_series = spy_df["close"]

    fip_rank = None
    if v3.get("use_fip_filter", False) or v3.get("use_fip_score", False):
        print("  Building FIP rank...")
        fip_rank = _build_fip_rank(price_data, int(v3.get("fip_period", 60)))

    print(f"  Done in {time.time() - t0:.1f}s")

    print("Generating Phase 4-v3 signals...")
    t0 = time.time()
    all_signals = []
    for sym, df in price_data.items():
        all_signals.extend(
            generate_factor_signals_v3(
                sym, df, p1, p2_filter, p4, v3, mom_rank, bbw_rank, spy_mom,
                high52_rank=high52_rank,
                spx_series=spx_series,
                fip_rank=fip_rank,
            )
        )

    use_fip_score = v3.get("use_fip_score", False) and fip_rank is not None
    fip_weight = float(v3.get("fip_score_weight", 0.3)) if use_fip_score else 0.0
    mom_weight = 1.0 - fip_weight

    for sig in all_signals:
        mom_val = 0.0
        if sig.entry_date in mom_rank.index and sig.symbol in mom_rank.columns:
            v = mom_rank.at[sig.entry_date, sig.symbol]
            mom_val = float(v) if pd.notna(v) else 0.0

        if use_fip_score and sig.entry_date in fip_rank.index and sig.symbol in fip_rank.columns:
            fip_val = fip_rank.at[sig.entry_date, sig.symbol]
            fip_val = float(fip_val) if pd.notna(fip_val) else 0.5
            sig.score = mom_weight * mom_val + fip_weight * fip_val
        else:
            sig.score = mom_val
    all_signals.sort(key=lambda s: (s.entry_date, -s.score))
    print(f"  {len(all_signals)} signals in {time.time() - t0:.1f}s")

    if not all_signals:
        print("No Phase 4-v3 signals.")
        return

    cfg_bt = copy.deepcopy(cfg)
    cfg_bt.setdefault("capital_mgmt", {})["target_invested_pct"] = 100
    trail_level = v3.get("trail_level", "donchian")
    trail_data = None
    if trail_level != "donchian":
        trail_data = build_cloud_trails(price_data, p2_filter, trail_level)

    print(f"Running backtest (trail={trail_level})...")
    t0 = time.time()
    result = run_backtest(all_signals, price_data, regime, cfg_bt, trail_data=trail_data)
    print(f"  Done in {time.time() - t0:.1f}s | {len(result.trades)} trades closed")

    metrics = compute_metrics(result)
    prefix = "phase4_v3"
    if args.tag:
        prefix += f"_{args.tag}"
    save_report(metrics, result, output_dir="backtest_results", prefix=prefix)

    print("\nPhase 4-v3 Results")
    _print_metrics(metrics)

    p4_csv = Path("backtest_results/phase4_trades.csv")
    if p4_csv.exists():
        old = pd.read_csv(p4_csv)
        wr = (old["r_multiple"] > 0).mean()
        wins = old.loc[old["r_multiple"] > 0, "r_multiple"].sum()
        losses = old.loc[old["r_multiple"] <= 0, "r_multiple"].abs().sum()
        pf = wins / losses if losses > 0 else float("inf")
        print(f"\nCompare Phase 4 old : WR={wr:.1%}, PF={pf:.4f}, trades={len(old)}")
        print(
            f"Compare Phase 4-v3  : WR={metrics['win_rate']:.1%}, "
            f"PF={metrics['profit_factor']}, trades={metrics['total_trades']}, "
            f"CAGR={metrics['cagr_pct']}%"
        )


if __name__ == "__main__":
    main()
