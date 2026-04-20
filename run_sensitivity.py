"""
Phase 1 파라미터 민감도 분석
핵심 2개 파라미터 × 3값씩 = 9조합을 실행하고 결과를 비교한다.
사용법: python run_sensitivity.py
"""
import itertools
import time
from pathlib import Path

import pandas as pd
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.indicators.regime import compute_regime
from src.strategy.breakout_pullback import generate_signals
from src.backtest.engine import run_backtest
from src.backtest.metrics import compute_metrics

GATE = {
    "total_trades":      (100,   "≥"),
    "win_rate":          (0.45,  "≥"),
    "profit_factor":     (1.3,   "≥"),
    "max_drawdown_pct":  (-15.0, "≥"),
    "sharpe":            (0.8,   "≥"),
}


def gate_str(metrics: dict) -> str:
    symbols = []
    for key, (threshold, op) in GATE.items():
        v = metrics[key]
        passed = v >= threshold if op == "≥" else v <= threshold
        symbols.append("✅" if passed else "❌")
    return " ".join(symbols)


def run_combo(price_data, regime, cfg, overrides: dict) -> dict:
    p1 = {**cfg["phase1_breakout_pullback"], **overrides}

    signals = []
    for sym, df in price_data.items():
        signals.extend(generate_signals(sym, df, p1))
    signals.sort(key=lambda s: s.entry_date)

    result = run_backtest(signals, price_data, regime, {**cfg, "phase1_breakout_pullback": p1})
    m = compute_metrics(result)
    return {**overrides, **{k: v for k, v in m.items() if k != "exit_reasons"}}


def main():
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]

    print("Loading price data from cache...")
    tickers = get_sp500_tickers()
    price_data = fetch_all(tickers, start, end, min_bars=252, refresh=False)
    print(f"  {len(price_data)} symbols loaded")

    print("Computing regime...")
    regime = compute_regime(
        start, end,
        ma_short=cfg["regime_filter"]["spy_ma_short"],
        ma_long=cfg["regime_filter"]["spy_ma_long"],
        vix_threshold=cfg["regime_filter"]["vix_threshold"],
    )

    # ── Parameter grid ────────────────────────────────────────────────────
    # Most impactful two parameters for win-rate / MDD tradeoff
    grid = {
        "donchian_period": [10, 20, 40],
        "stop_atr_mult":   [1.5, 2.0, 2.5],
    }
    combos = [
        dict(zip(grid.keys(), vals))
        for vals in itertools.product(*grid.values())
    ]
    print(f"\nRunning {len(combos)} combinations (donchian × stop_atr)...\n")

    header = f"{'don':>4} {'stop_atr':>8} {'trades':>6} {'WR':>6} {'PF':>5} {'ret%':>6} {'MDD%':>6} {'sharpe':>6} {'gates'}"
    print(header)
    print("─" * len(header))

    rows = []
    for combo in combos:
        t0 = time.time()
        row = run_combo(price_data, regime, cfg, combo)
        elapsed = time.time() - t0
        rows.append(row)
        print(
            f"{row['donchian_period']:>4} {row['stop_atr_mult']:>8.1f}"
            f" {row['total_trades']:>6} {row['win_rate']:>6.1%}"
            f" {row['profit_factor']:>5.2f} {row['total_return_pct']:>6.1f}"
            f" {row['max_drawdown_pct']:>6.1f} {row['sharpe']:>6.3f}"
            f"  {gate_str(row)}  ({elapsed:.0f}s)"
        )

    # ── Save results ──────────────────────────────────────────────────────
    out = Path("backtest_results")
    out.mkdir(exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / "phase1_sensitivity.csv", index=False)

    # ── Best combos ───────────────────────────────────────────────────────
    print("\n── Top 3 by Sharpe ──────────────────────────────────────────────")
    top = df.sort_values("sharpe", ascending=False).head(3)
    for _, r in top.iterrows():
        print(
            f"  don={int(r['donchian_period'])}, stop_atr={r['stop_atr_mult']:.1f}"
            f"  →  WR={r['win_rate']:.1%}  PF={r['profit_factor']:.2f}"
            f"  MDD={r['max_drawdown_pct']:.1f}%  Sharpe={r['sharpe']:.3f}  {gate_str(r.to_dict())}"
        )

    print("\n── All gate flags: trades WR PF MDD Sharpe ─────────────────────")

    # Recommend best robust combo
    passed = df[
        (df["total_trades"]     >= 100)   &
        (df["profit_factor"]    >= 1.3)   &
        (df["max_drawdown_pct"] >= -15.0)
    ].sort_values("sharpe", ascending=False)

    if not passed.empty:
        best = passed.iloc[0]
        print(f"\n✅ 추천 파라미터: don={int(best['donchian_period'])}, stop_atr={best['stop_atr_mult']:.1f}")
        print(f"   → 이 조합을 config.yaml에 반영하고 WFO 진행 권장")
        print(f"   → Phase 게이트 통과 후 Phase 2 진행 가능")
    else:
        print("\n❌ 모든 조합이 MDD 또는 PF 게이트 미달")
        print("   → 구조적 문제일 수 있음 — Phase 2와 비교 후 판단 권장")

    print(f"\n결과 저장: {out}/phase1_sensitivity.csv")


if __name__ == "__main__":
    main()
