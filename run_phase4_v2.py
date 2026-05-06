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
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    p   = cfg["phase4_v2_anticipatory"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]

    if args.slope_method:
        p["slope_method"] = args.slope_method

    slope_label = p["slope_method"]
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

    print(f"Running backtest (touch_hold={max_hold}bars, total_timeout={max_total}bars, cloud_exit={exit_level})...")
    t0 = time.time()
    result = run_anticipatory_backtest(
        all_signals, price_data, cloud_data, regime, cfg_bt,
        max_hold_bars=max_hold, max_total_bars=max_total, cloud_exit_level=exit_level,
    )
    print(f"  Done in {time.time()-t0:.1f}s | {len(result.trades)} trades closed")

    # ── 7. Metrics ────────────────────────────────────────────────────────
    m = compute_metrics(result)
    prefix = f"phase4_v2_{slope_label}"
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
        print(f"  {'✅' if passed else '❌'} {desc}")

    # ── 9. Phase 4 비교 ───────────────────────────────────────────────────
    p4_csv = Path("backtest_results/phase4_trades.csv")
    if p4_csv.exists():
        df_p4 = pd.read_csv(p4_csv)
        p4_wr = (df_p4["r_multiple"] > 0).mean()
        p4_pf_num = df_p4.loc[df_p4["r_multiple"] > 0, "r_multiple"].sum()
        p4_pf_den = df_p4.loc[df_p4["r_multiple"] <= 0, "r_multiple"].abs().sum()
        p4_pf = round(p4_pf_num / p4_pf_den, 4) if p4_pf_den > 0 else float("inf")
        print(f"\n  비교 — Phase 4 (기존): WR={p4_wr:.1%}, PF={p4_pf}, trades={len(df_p4)}")
        print(f"  비교 — Phase 4-v2   : WR={m['win_rate']:.1%}, PF={m['profit_factor']}, "
              f"trades={m['total_trades']}, CAGR={m['cagr_pct']}%")


if __name__ == "__main__":
    main()
