"""
Phase 4-v2: 일목구름 선접근(Anticipatory Cloud) 백테스트

사용법:
  python run_phase4_v2.py [--slope-method reg10|avg5] [--tickers AAPL MSFT ...] [--refresh]

slope_method:
  reg10 — 최근 10일 선형회귀로 기울기 추정 (기본)
  avg5  — 최근 5일 평균 변화율로 기울기 추정
"""
import argparse
import copy
import time
from pathlib import Path

import pandas as pd
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.regime import compute_regime
from src.indicators.factors import build_factor_matrices
from src.indicators.ichimoku import ichimoku
from src.strategy.factor_stack_v2 import generate_anticipatory_factor_signals
from src.backtest.anticipatory_engine import run_anticipatory_backtest
from src.backtest.metrics import compute_metrics, save_report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slope-method", choices=["reg10", "avg5"], default=None,
                        help="기울기 추정 방식 (config 기본값 덮어쓰기)")
    parser.add_argument("--entry-mode", choices=["anticipatory", "confirmed_support"], default=None)
    parser.add_argument("--max-total-bars", type=int, default=None)
    parser.add_argument("--cloud-exit-level", choices=["top", "mid", "bottom"], default=None)
    parser.add_argument("--targets", nargs=2, type=float, metavar=("T1_R", "T2_R"))
    parser.add_argument("--fast-fail-bars", type=int, default=None)
    parser.add_argument("--fast-fail-min-r", type=float, default=None)
    parser.add_argument("--stop-method", choices=["cloud", "atr", "hybrid"], default=None)
    parser.add_argument("--stop-atr-mult", type=float, default=None)
    parser.add_argument("--eta-max", type=int, default=None)
    parser.add_argument("--eta-min", type=int, default=None)
    parser.add_argument("--cloud-thickness", type=float, default=None, metavar="PCT")
    parser.add_argument("--no-rising-lows", action="store_true")
    parser.add_argument("--max-current-gap", type=float, default=None,
                        metavar="PCT", help="max_current_cloud_gap_pct override")
    parser.add_argument("--max-future-gap", type=float, default=None,
                        metavar="PCT", help="max_future_cloud_gap_pct override")
    parser.add_argument("--no-rising-cloud", action="store_true",
                        help="require_rising_cloud = False")
    parser.add_argument("--max-upslope", type=float, default=None,
                        metavar="PCT", help="max_upslope_pct_per_day override (박스권 상한)")
    parser.add_argument("--box-range", type=float, default=None,
                        metavar="PCT", help="box_range_max_pct override (박스권 최대 범위 %)")
    parser.add_argument("--box-lookback", type=int, default=None,
                        help="box_range_lookback override (박스권 판정 기간)")
    parser.add_argument("--eta-reach", type=float, default=None,
                        metavar="PCT", help="eta_reach_pct override (구름 도달 판정 %)")
    parser.add_argument("--use-adx-filter", action="store_true")
    parser.add_argument("--adx-min", type=float, default=None)
    parser.add_argument("--adx-rising", action="store_true")
    parser.add_argument("--use-52w-high-filter", action="store_true")
    parser.add_argument("--high52-min-ratio", type=float, default=None)
    parser.add_argument("--tag", default=None, help="output prefix suffix for experiments")
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    p   = cfg["phase4_v2_anticipatory"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]

    if args.slope_method:
        p["slope_method"] = args.slope_method
    if args.entry_mode:
        p["entry_mode"] = args.entry_mode
    if args.max_total_bars is not None:
        p["max_total_bars"] = args.max_total_bars
    if args.cloud_exit_level:
        p["cloud_exit_level"] = args.cloud_exit_level
    if args.targets:
        p["partial_exit_r_multiples"] = list(args.targets)
    if args.fast_fail_bars is not None:
        p["max_touch_fail_bars"] = args.fast_fail_bars
    if args.fast_fail_min_r is not None:
        p["min_touch_bounce_r"] = args.fast_fail_min_r
    if args.stop_method:
        p["stop_method"] = args.stop_method
    if args.stop_atr_mult is not None:
        p["stop_atr_mult"] = args.stop_atr_mult
    if args.eta_max is not None:
        p["eta_max"] = args.eta_max
    if args.eta_min is not None:
        p["eta_min"] = args.eta_min
    if args.cloud_thickness is not None:
        p["cloud_thickness_min_pct"] = args.cloud_thickness
    if args.no_rising_lows:
        p["require_rising_lows"] = False
    if args.max_current_gap is not None:
        p["max_current_cloud_gap_pct"] = args.max_current_gap
    if args.max_future_gap is not None:
        p["max_future_cloud_gap_pct"] = args.max_future_gap
    if args.no_rising_cloud:
        p["require_rising_cloud"] = False
    if args.max_upslope is not None:
        p["max_upslope_pct_per_day"] = args.max_upslope
    if args.box_range is not None:
        p["box_range_max_pct"] = args.box_range
    if args.box_lookback is not None:
        p["box_range_lookback"] = args.box_lookback
    if args.eta_reach is not None:
        p["eta_reach_pct"] = args.eta_reach
    if args.use_adx_filter:
        p["use_adx_filter"] = True
    if args.adx_min is not None:
        p["adx_min"] = args.adx_min
    if args.adx_rising:
        p["adx_require_rising"] = True
    if args.use_52w_high_filter:
        p["use_52w_high_filter"] = True
    if args.high52_min_ratio is not None:
        p["high52_min_ratio"] = args.high52_min_ratio

    slope_label = p["slope_method"]
    entry_label = p.get("entry_mode", "anticipatory")
    print(f"\n── Phase 4-v2 | slope_method={slope_label} ──────────────────────────")

    # ── 1. Data ───────────────────────────────────────────────────────────
    tickers = args.tickers or get_sp500_tickers()
    print(f"Loading price data ({len(tickers)} tickers)...")
    price_data = fetch_all(tickers, start, end, min_bars=300, refresh=args.refresh)
    print(f"  {len(price_data)} symbols loaded")

    # ── 2. Regime ─────────────────────────────────────────────────────────
    print("Computing regime...")
    regime = compute_regime(
        start, end,
        ma_short=cfg["regime_filter"]["spy_ma_short"],
        ma_long=cfg["regime_filter"]["spy_ma_long"],
        vix_threshold=cfg["regime_filter"]["vix_threshold"],
    )

    # ── 3. SPY momentum (RS 필터용) ───────────────────────────────────────
    print("Building SPY momentum...")
    _, _, spy_mom = build_factor_matrices(
        price_data,
        mom_period=p.get("momentum_period", 63),
        bb_period=20,
    )

    # ── 4. Cloud data (엔진 cloud_mid 청산용) ────────────────────────────
    print("Pre-computing cloud data for exit logic...")
    cloud_data: dict[str, pd.DataFrame] = {}
    tenkan_p   = p.get("tenkan_period", 9)
    kijun_p    = p.get("kijun_period", 26)
    senkou_b_p = p.get("senkou_b_period", 52)
    shift      = p.get("chikou_offset", 26)
    for sym, df in price_data.items():
        if len(df) < senkou_b_p + shift:
            continue
        ich = ichimoku(df, tenkan_p, kijun_p, senkou_b_p, shift)
        cloud_data[sym] = ich[["senkou_a", "senkou_b"]]

    # ── 5. Signals ────────────────────────────────────────────────────────
    print("Generating anticipatory signals...")
    t0 = time.time()
    all_signals = []
    for sym, df in price_data.items():
        sigs = generate_anticipatory_factor_signals(sym, df, p, spy_mom)
        all_signals.extend(sigs)

    all_signals.sort(key=lambda s: s.entry_date)
    print(f"  {len(all_signals)} signals in {time.time()-t0:.1f}s")

    if not all_signals:
        print("⚠️  신호 없음 — eta_min/max, max_distance_pct, slope_method 파라미터 확인 요")
        return

    # ── 6. Backtest ───────────────────────────────────────────────────────
    cfg_bt = copy.deepcopy(cfg)
    cfg_bt.setdefault("capital_mgmt", {})["target_invested_pct"] = 100
    max_hold       = p.get("max_hold_bars", 3)    # 구름 터치 후
    max_total      = p.get("max_total_bars", 15)  # 전체 안전망
    exit_level     = p.get("cloud_exit_level", "bottom")
    fast_fail_bars = p.get("max_touch_fail_bars", 0)
    fast_fail_minr = p.get("min_touch_bounce_r", 0.0)

    print(f"Running backtest (touch_hold={max_hold}bars, total_timeout={max_total}bars, cloud_exit={exit_level})...")
    t0 = time.time()
    result = run_anticipatory_backtest(
        all_signals, price_data, cloud_data, regime, cfg_bt,
        max_hold_bars=max_hold,
        max_total_bars=max_total,
        cloud_exit_level=exit_level,
        max_touch_fail_bars=fast_fail_bars,
        min_touch_bounce_r=fast_fail_minr,
    )
    print(f"  Done in {time.time()-t0:.1f}s | {len(result.trades)} trades closed")

    # ── 7. Metrics ────────────────────────────────────────────────────────
    m = compute_metrics(result)
    prefix = f"phase4_v2_{slope_label}"
    if entry_label != "anticipatory":
        prefix += f"_{entry_label}"
    if args.tag:
        prefix += f"_{args.tag}"
    save_report(m, result, output_dir="backtest_results", prefix=prefix)

    print(f"\n── Phase 4-v2 ({slope_label}) Results ───────────────────────────")
    skip = {"exit_reasons"}
    for k, v in m.items():
        if k not in skip:
            print(f"  {k:30s}: {v}")

    print("\n  Exit reasons:")
    for reason, cnt in m.get("exit_reasons", {}).items():
        print(f"    {reason:30s}: {cnt}")

    # ── 8. Gates ──────────────────────────────────────────────────────────
    print("\nPhase 4-v2 게이트:")
    gates = {
        "Trades ≥ 50":          m["total_trades"]    >= 50,
        "Win rate ≥ 35%":       m["win_rate"]         >= 0.35,
        "Profit Factor ≥ 1.3":  m["profit_factor"]    >= 1.3,
        "MDD ≥ -20%":           m["max_drawdown_pct"] >= -20,
        "Sharpe ≥ 0.5":         m["sharpe"]           >= 0.5,
    }
    for desc, passed in gates.items():
        print(f"  {'OK' if passed else 'NO'} {desc}")

    # ── 9. Phase 4 비교 ───────────────────────────────────────────────────
    p4_csv = Path("backtest_results/phase4_trades.csv")
    if p4_csv.exists():
        df_p4 = pd.read_csv(p4_csv)
        p4_wr = (df_p4["r_multiple"] > 0).mean()
        p4_pf_num = df_p4.loc[df_p4["r_multiple"] > 0, "r_multiple"].sum()
        p4_pf_den = df_p4.loc[df_p4["r_multiple"] <= 0, "r_multiple"].abs().sum()
        p4_pf = round(p4_pf_num / p4_pf_den, 4) if p4_pf_den > 0 else float("inf")
        print(f"\n  Compare Phase 4 old : WR={p4_wr:.1%}, PF={p4_pf}, trades={len(df_p4)}")
        print(f"  Compare Phase 4-v2  : WR={m['win_rate']:.1%}, PF={m['profit_factor']}, "
              f"trades={m['total_trades']}, CAGR={m['cagr_pct']}%")


if __name__ == "__main__":
    main()
