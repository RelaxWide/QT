"""
Phase 4: Phase 3 Hybrid × Factor Stacking 백테스트
사용법: python run_phase4.py [--tickers AAPL MSFT ...]
"""
import argparse
import time
from pathlib import Path

import pandas as pd
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.regime import compute_regime
from src.indicators.factors import build_factor_matrices
from src.strategy.factor_stack import generate_factor_signals
from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics, save_report


def load_phase_metrics_from_csv(csv_path: Path) -> dict | None:
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path)
    wr   = (df["r_multiple"] > 0).mean()
    wins = df.loc[df["r_multiple"] > 0, "r_multiple"].sum()
    loss = df.loc[df["r_multiple"] <= 0, "r_multiple"].abs().sum()
    return {
        "total_trades":   len(df),
        "win_rate":       round(wr, 4),
        "avg_r":          round(df["r_multiple"].mean(), 4),
        "profit_factor":  round(wins / loss, 4) if loss > 0 else float("inf"),
        "total_return_pct": "─",
        "max_drawdown_pct": "─",
        "sharpe": "─",
    }


def compare_phases(metrics_map: dict[str, dict]) -> None:
    keys = ["total_trades", "win_rate", "avg_r", "profit_factor",
            "total_return_pct", "max_drawdown_pct", "sharpe", "sortino"]
    labels = list(metrics_map.keys())
    header = f"{'지표':28s}" + "".join(f"{lb:>12}" for lb in labels)
    print(f"\n{header}")
    print("─" * (28 + 12 * len(labels)))
    for k in keys:
        row = f"  {k:26s}"
        for lb in labels:
            v = metrics_map[lb].get(k, "─") if metrics_map[lb] else "─"
            row += f"{str(v):>12}"
        print(row)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    p1    = cfg["phase1_breakout_pullback"]
    p2    = cfg["phase2_cloud_support"]
    p3    = cfg["phase3_hybrid"]
    p4    = cfg["phase4_factor_stack"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]

    p2_filter = dict(p2)
    p2_filter["cloud_filter_thickness_min_pct"] = p3["cloud_filter_thickness_min_pct"]
    p2_filter["cloud_filter_use_chikou"]        = p3["cloud_filter_use_chikou"]
    p4["momentum_period"] = p4.get("momentum_period", 63)

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

    # ── 3. Factor matrices ────────────────────────────────────────────────
    print("Building factor matrices (momentum + BB width)...")
    t0 = time.time()
    mom_rank, bbw_rank, spy_mom = build_factor_matrices(
        price_data,
        mom_period=p4["momentum_period"],
        bb_period=p4["bbwidth_period"],
    )
    print(f"  Done in {time.time()-t0:.1f}s")

    # ── 4. Signals ────────────────────────────────────────────────────────
    print("Generating factor-stacked signals...")
    t0 = time.time()
    all_signals = []
    for sym, df in price_data.items():
        sigs = generate_factor_signals(
            sym, df, p1, p2_filter, p4, mom_rank, bbw_rank, spy_mom
        )
        all_signals.extend(sigs)
    all_signals.sort(key=lambda s: s.entry_date)
    print(f"  {len(all_signals)} signals in {time.time()-t0:.1f}s")

    if not all_signals:
        print("⚠️  시그널 없음 — min_factors_required 값을 낮추거나 각 필터 비활성화 검토")
        return

    # ── 5. Backtest ───────────────────────────────────────────────────────
    print("Running backtest...")
    t0 = time.time()
    result = run_backtest(all_signals, price_data, regime, cfg)
    print(f"  Done in {time.time()-t0:.1f}s | {len(result.trades)} trades closed")

    # ── 6. Metrics ────────────────────────────────────────────────────────
    m4 = compute_metrics(result)
    save_report(m4, result, output_dir="backtest_results", prefix="phase4")

    print("\n── Phase 4 Results ──────────────────────────────────")
    for k, v in m4.items():
        if k != "exit_reasons":
            print(f"  {k:30s}: {v}")

    print("\nPhase 게이트:")
    gates = {
        "Trades ≥ 100":        m4["total_trades"]    >= 100,
        "Win rate ≥ 33%":      m4["win_rate"]         >= 0.33,
        "Profit Factor ≥ 1.5": m4["profit_factor"]    >= 1.5,
        "MDD ≥ -15%":          m4["max_drawdown_pct"] >= -15,
        "Sharpe ≥ 0.8":        m4["sharpe"]           >= 0.8,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")

    # ── 7. 비교 ───────────────────────────────────────────────────────────
    m1 = load_phase_metrics_from_csv(Path("backtest_results/phase1_trades.csv"))
    m3 = load_phase_metrics_from_csv(Path("backtest_results/phase3_trades.csv"))
    compare_phases({"Phase1": m1, "Phase3": m3, "Phase4": m4})

    all_passed = all(gates.values())
    if all_passed:
        print("\n✅ Phase 4 게이트 통과 — Paper Trading 준비 단계로 진입 가능")
    else:
        print("\n❌ Phase 4 게이트 미달 — min_factors_required 또는 팩터 임계값 조정 권장")
        print("   힌트: min_factors_required: 1 또는 use_bbwidth: false 시도")


if __name__ == "__main__":
    main()
