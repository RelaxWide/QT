"""
Phase 3: Phase 1 돌파-풀백 × Ichimoku 구름 필터 Hybrid 백테스트
사용법: python run_phase3.py [--tickers AAPL MSFT ...]
"""
import argparse
import time
from pathlib import Path

import pandas as pd
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.regime import compute_regime
from src.strategy.hybrid import generate_hybrid_signals
from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics, save_report


def load_phase_metrics_from_csv(csv_path: Path) -> dict | None:
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path)
    wr = (df["r_multiple"] > 0).mean()
    wins = df.loc[df["r_multiple"] > 0, "r_multiple"].sum()
    loss = df.loc[df["r_multiple"] <= 0, "r_multiple"].abs().sum()
    return {
        "total_trades":    len(df),
        "win_rate":        round(wr, 4),
        "avg_r":           round(df["r_multiple"].mean(), 4),
        "profit_factor":   round(wins / loss, 4) if loss > 0 else float("inf"),
        "total_return_pct": "─",
        "max_drawdown_pct": "─",
        "sharpe":          "─",
        "sortino":         "─",
    }


def compare_all(m1: dict, m2: dict, m3: dict) -> None:
    keys = ["total_trades", "win_rate", "avg_r", "profit_factor",
            "total_return_pct", "max_drawdown_pct", "sharpe", "sortino"]
    print(f"\n{'지표':28s} {'Phase1':>10} {'Phase2':>10} {'Phase3':>10}")
    print("─" * 64)
    for k in keys:
        v1 = m1.get(k, "─") if m1 else "─"
        v2 = m2.get(k, "─") if m2 else "─"
        v3 = m3.get(k, "─")
        print(f"  {k:26s} {str(v1):>10} {str(v2):>10} {str(v3):>10}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    p1    = cfg["phase1_breakout_pullback"]
    p2    = cfg["phase2_cloud_support"]
    p3    = cfg["phase3_hybrid"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]

    # p2 파라미터에 p3 오버라이드 적용
    p2_filter = dict(p2)
    p2_filter["cloud_filter_thickness_min_pct"] = p3["cloud_filter_thickness_min_pct"]
    p2_filter["cloud_filter_use_chikou"]        = p3["cloud_filter_use_chikou"]

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

    # ── 3. Signals ────────────────────────────────────────────────────────
    print("Generating hybrid signals (Phase 1 × cloud filter)...")
    t0 = time.time()
    all_signals = []
    for sym, df in price_data.items():
        sigs = generate_hybrid_signals(sym, df, p1, p2_filter)
        all_signals.extend(sigs)
    all_signals.sort(key=lambda s: s.entry_date)
    print(f"  {len(all_signals)} signals in {time.time()-t0:.1f}s")

    if not all_signals:
        print("⚠️  시그널 없음 — cloud_filter_thickness_min_pct 값을 낮춰보세요")
        return

    # ── 4. Backtest (Phase 1 trail: donchian lower) ───────────────────────
    print("Running backtest...")
    t0 = time.time()
    result = run_backtest(all_signals, price_data, regime, cfg)
    print(f"  Done in {time.time()-t0:.1f}s | {len(result.trades)} trades closed")

    # ── 5. Metrics & Report ───────────────────────────────────────────────
    m3 = compute_metrics(result)
    save_report(m3, result, output_dir="backtest_results", prefix="phase3")

    print("\n── Phase 3 Results ──────────────────────────────────")
    for k, v in m3.items():
        if k != "exit_reasons":
            print(f"  {k:30s}: {v}")

    print("\nPhase 게이트:")
    gates = {
        "Trades ≥ 100":        m3["total_trades"]    >= 100,
        "Win rate ≥ 33%":      m3["win_rate"]         >= 0.33,
        "Profit Factor ≥ 1.5": m3["profit_factor"]    >= 1.5,
        "MDD ≥ -15%":          m3["max_drawdown_pct"] >= -15,
        "Sharpe ≥ 0.8":        m3["sharpe"]           >= 0.8,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")

    # ── 6. 3-way 비교 ─────────────────────────────────────────────────────
    m1 = load_phase_metrics_from_csv(Path("backtest_results/phase1_trades.csv"))
    m2 = load_phase_metrics_from_csv(Path("backtest_results/phase2_trades.csv"))
    compare_all(m1, m2, m3)

    all_passed = all(gates.values())
    if all_passed:
        print("\n✅ Phase 3 게이트 통과 — Phase 4 Factor Stacking 진행 가능")
    else:
        print("\n❌ Phase 3 게이트 미달 — cloud_filter_thickness_min_pct 조정 권장")


if __name__ == "__main__":
    main()
