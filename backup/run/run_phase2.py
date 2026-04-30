"""
Phase 2: 박스권 + 상승 구름 상단 지지 백테스트 및 Phase 1 비교
사용법: python run_phase2.py [--tickers AAPL MSFT ...]
"""
import argparse
import time
from pathlib import Path

import pandas as pd
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.ichimoku import ichimoku
from src.indicators.regime import compute_regime
from src.strategy.cloud_support import generate_cloud_signals
from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics, save_report


def build_tenkan_trail(price_data: dict, tenkan_period: int) -> dict[str, pd.Series]:
    """Phase 2 트레일: 전환선 하향 이탈 시 청산."""
    trail = {}
    for sym, df in price_data.items():
        ich = ichimoku(df, tenkan_period=tenkan_period)
        trail[sym] = ich["tenkan"]
    return trail


def compare_phases(m1: dict, m2: dict) -> None:
    keys = ["total_trades", "win_rate", "avg_r", "profit_factor",
            "total_return_pct", "max_drawdown_pct", "sharpe", "sortino"]
    print(f"\n{'지표':30s} {'Phase 1':>10} {'Phase 2':>10} {'차이':>10}")
    print("─" * 65)
    for k in keys:
        v1, v2 = m1.get(k, 0), m2.get(k, 0)
        diff = v2 - v1 if isinstance(v1, (int, float)) else "─"
        sign = "+" if isinstance(diff, float) and diff > 0 else ""
        print(f"  {k:28s} {v1:>10} {v2:>10} {sign}{diff if isinstance(diff, float) else diff:>9}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg  = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    p2   = cfg["phase2_cloud_support"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]

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
    print("Generating cloud support signals...")
    t0 = time.time()
    all_signals = []
    for sym, df in price_data.items():
        sigs = generate_cloud_signals(sym, df, p2)
        all_signals.extend(sigs)
    all_signals.sort(key=lambda s: s.entry_date)
    print(f"  {len(all_signals)} signals in {time.time()-t0:.1f}s")

    if not all_signals:
        print("⚠️  시그널 없음 — cloud_thickness_min_pct / box_width_max_pct 기준을 낮춰보세요")
        return

    # ── 4. Trail data: 전환선(tenkan) ─────────────────────────────────────
    print("Precomputing tenkan trail...")
    trail_data = build_tenkan_trail(price_data, p2["tenkan_period"])

    # ── 5. Backtest ───────────────────────────────────────────────────────
    print("Running backtest...")
    t0 = time.time()
    result = run_backtest(all_signals, price_data, regime, cfg, trail_data=trail_data)
    print(f"  Done in {time.time()-t0:.1f}s | {len(result.trades)} trades closed")

    # ── 6. Metrics ────────────────────────────────────────────────────────
    m2 = compute_metrics(result)
    save_report(m2, result, output_dir="backtest_results")

    # Rename Phase 2 outputs
    for old, new in [
        ("backtest_results/phase1_trades.csv",  "backtest_results/phase2_trades.csv"),
        ("backtest_results/phase1_equity.csv",  "backtest_results/phase2_equity.csv"),
        ("backtest_results/phase1_equity.png",  "backtest_results/phase2_equity.png"),
        ("backtest_results/phase1_report.md",   "backtest_results/phase2_report.md"),
    ]:
        p_old, p_new = Path(old), Path(new)
        if p_old.exists():
            p_old.rename(p_new)

    print("\n── Phase 2 Results ──────────────────────────────────")
    for k, v in m2.items():
        if k != "exit_reasons":
            print(f"  {k:30s}: {v}")

    print("\nPhase 게이트:")
    gates = {
        "Trades ≥ 100":          m2["total_trades"]     >= 100,
        "Win rate ≥ 33%":        m2["win_rate"]          >= 0.33,
        "Profit Factor ≥ 1.5":   m2["profit_factor"]     >= 1.5,
        "MDD ≥ -15%":            m2["max_drawdown_pct"]  >= -15,
        "Sharpe ≥ 0.8":          m2["sharpe"]            >= 0.8,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")

    # ── 7. Phase 1 vs Phase 2 비교 ────────────────────────────────────────
    phase1_csv = Path("backtest_results/phase1_trades.csv")
    if not phase1_csv.exists():
        # Phase 1 결과로 메트릭 재계산
        print("\n(Phase 1 결과 없음 — 비교 생략. run_backtest.py 먼저 실행)")
    else:
        trades_p1 = pd.read_csv(phase1_csv)
        # 단순 통계 비교 (전체 재실행 없이 csv에서)
        wr1 = (trades_p1["r_multiple"] > 0).mean()
        pf1_w = trades_p1.loc[trades_p1["r_multiple"] > 0, "r_multiple"].sum()
        pf1_l = trades_p1.loc[trades_p1["r_multiple"] <= 0, "r_multiple"].abs().sum()
        pf1 = pf1_w / pf1_l if pf1_l > 0 else float("inf")
        m1_approx = {
            "total_trades": len(trades_p1),
            "win_rate": round(wr1, 4),
            "avg_r": round(trades_p1["r_multiple"].mean(), 4),
            "profit_factor": round(pf1, 4),
            "total_return_pct": "─ (run_backtest.py)",
            "max_drawdown_pct": "─",
            "sharpe": "─",
            "sortino": "─",
        }
        compare_phases(m1_approx, m2)

    all_passed = all(gates.values())
    if all_passed:
        print("\n✅ Phase 2 게이트 통과 — Phase 3 Hybrid 설계 진행 가능")
    else:
        print("\n❌ Phase 2 게이트 미달 — 파라미터 조정 또는 원인 분석 필요")
        print("   config.yaml의 cloud_thickness_min_pct / box_width_max_pct 조정 권장")


if __name__ == "__main__":
    main()
