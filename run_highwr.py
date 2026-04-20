"""
High Win-Rate 전략: MA 눌림목 반등 백테스트
사용법: python run_highwr.py [--tickers AAPL MSFT ...]
"""
import argparse
import time
from pathlib import Path

import pandas as pd
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.regime import compute_regime
from src.strategy.ma_bounce import generate_ma_bounce_signals
from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics, save_report


def build_ma50_trail(price_data: dict, period: int) -> dict[str, pd.Series]:
    """트레일 기준: MA50 하향 이탈 시 청산."""
    return {
        sym: df["close"].rolling(period).mean().shift(1)
        for sym, df in price_data.items()
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    hw    = cfg["highwr_ma_bounce"]
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
    print("Generating MA bounce signals...")
    t0 = time.time()
    all_signals = []
    for sym, df in price_data.items():
        all_signals.extend(generate_ma_bounce_signals(sym, df, hw))
    all_signals.sort(key=lambda s: s.entry_date)
    print(f"  {len(all_signals)} signals in {time.time()-t0:.1f}s")

    if not all_signals:
        print("⚠️  시그널 없음 — 파라미터 완화 필요")
        return

    # ── 4. Trail: MA50 ────────────────────────────────────────────────────
    trail_data = build_ma50_trail(price_data, hw["trail_ma_period"])

    # ── 5. Backtest ───────────────────────────────────────────────────────
    print("Running backtest...")
    t0 = time.time()
    result = run_backtest(all_signals, price_data, regime, cfg, trail_data=trail_data)
    print(f"  Done in {time.time()-t0:.1f}s | {len(result.trades)} trades closed")

    # ── 6. Metrics ────────────────────────────────────────────────────────
    m = compute_metrics(result)
    save_report(m, result, output_dir="backtest_results", prefix="highwr")

    print("\n── High WR Results ──────────────────────────────────")
    for k, v in m.items():
        if k != "exit_reasons":
            print(f"  {k:30s}: {v}")

    print("\n게이트 체크:")
    gates = {
        "Trades ≥ 100":        m["total_trades"]    >= 100,
        "Win rate ≥ 50%":      m["win_rate"]         >= 0.50,
        "Profit Factor ≥ 1.3": m["profit_factor"]    >= 1.3,
        "MDD ≥ -15%":          m["max_drawdown_pct"] >= -15,
        "Sharpe ≥ 0.8":        m["sharpe"]           >= 0.8,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")

    # Phase 4와 비교
    p4_csv = Path("backtest_results/phase4_trades.csv")
    if p4_csv.exists():
        p4 = pd.read_csv(p4_csv)
        p4_wr = (p4["r_multiple"] > 0).mean()
        p4_pf = p4.loc[p4["r_multiple"]>0,"r_multiple"].sum() / p4.loc[p4["r_multiple"]<=0,"r_multiple"].abs().sum()
        print(f"\n비교:")
        print(f"  {'':28s} {'Phase4':>10} {'HighWR':>10}")
        print(f"  {'total_trades':28s} {len(p4):>10} {m['total_trades']:>10}")
        print(f"  {'win_rate':28s} {p4_wr:>10.1%} {m['win_rate']:>10.1%}")
        print(f"  {'profit_factor':28s} {p4_pf:>10.2f} {m['profit_factor']:>10.2f}")
        print(f"  {'avg_win_r':28s} {'─':>10} {m['avg_win_r']:>10.4f}")
        print(f"  {'avg_loss_r':28s} {'─':>10} {m['avg_loss_r']:>10.4f}")

    all_passed = all(gates.values())
    if all_passed:
        print("\n✅ 게이트 통과 — 민감도 분석 또는 Paper Trading 진행 가능")
    else:
        print("\n❌ 게이트 미달 — 파라미터 조정 필요")
        if m["win_rate"] < 0.50:
            print("   → rsi_high 낮추기 또는 pullback_max_bars 줄이기 시도")
        if m["profit_factor"] < 1.3:
            print("   → target_r_multiple 높이기 또는 bounce_volume_mult 높이기 시도")


if __name__ == "__main__":
    main()
