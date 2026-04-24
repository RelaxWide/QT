"""
IBS 평균회귀 전략 백테스트
사용법: python run_ibs.py [--tickers AAPL MSFT ...]
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.fetch.universe import get_sp500_tickers
from src.fetch.prices import fetch_all
from src.strategy.ibs_mean_reversion import generate_ibs_signals
from src.backtest.ibs_engine import run_ibs_backtest
from src.backtest.metrics import compute_metrics, save_report


def compute_ibs_extra(result) -> dict:
    """tail_ratio, max_losing_streak — IBS 게이트 추가 지표"""
    r_mults = [t.r_multiple for t in result.trades]
    if not r_mults:
        return {"tail_ratio": 0.0, "max_losing_streak": 0}

    arr = np.array(r_mults)
    top5  = np.percentile(arr, 95)
    bot5  = abs(np.percentile(arr, 5))
    tail_ratio = (top5 / bot5) if bot5 > 0 else float("inf")

    streak = max_streak = 0
    for r in r_mults:
        if r <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    return {"tail_ratio": round(tail_ratio, 4), "max_losing_streak": max_streak}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    ibs_p = cfg["ibs_strategy"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]

    # ── 1. 데이터 ─────────────────────────────────────────────────────────
    tickers = args.tickers or get_sp500_tickers()
    if "SPY" not in tickers:
        tickers = ["SPY"] + list(tickers)

    print(f"Loading price data ({len(tickers)} tickers)...")
    price_data = fetch_all(tickers, start, end, min_bars=250, refresh=args.refresh)
    print(f"  {len(price_data)} symbols loaded")

    spy_close = price_data["SPY"]["close"] if "SPY" in price_data else None
    if spy_close is None:
        print("❌ SPY 데이터 없음 — 레짐 필터 불가")
        return

    # ── 2. 시그널 생성 ────────────────────────────────────────────────────
    print("Generating IBS signals...")
    t0 = time.time()
    all_signals = []
    for sym, df in price_data.items():
        if sym == "SPY":
            continue
        all_signals.extend(generate_ibs_signals(sym, df, ibs_p))
    all_signals.sort(key=lambda s: s.entry_date)
    print(f"  {len(all_signals)} signals in {time.time()-t0:.1f}s")

    if not all_signals:
        print("⚠️  시그널 없음 — 파라미터 완화 필요")
        return

    # ── 3. 백테스트 ───────────────────────────────────────────────────────
    print("Running IBS backtest...")
    t0 = time.time()
    result = run_ibs_backtest(all_signals, price_data, spy_close, cfg)
    print(f"  Done in {time.time()-t0:.1f}s | {len(result.trades)} trades closed")

    # ── 4. 지표 계산 ──────────────────────────────────────────────────────
    m     = compute_metrics(result)
    extra = compute_ibs_extra(result)
    save_report(m, result, output_dir="backtest_results", prefix="ibs")

    print("\n── IBS Mean Reversion Results ────────────────────────")
    for k, v in m.items():
        if k != "exit_reasons":
            print(f"  {k:30s}: {v}")
    print(f"  {'tail_ratio':30s}: {extra['tail_ratio']}")
    print(f"  {'max_losing_streak':30s}: {extra['max_losing_streak']}")

    print("\n청산 사유:")
    for reason, cnt in m.get("exit_reasons", {}).items():
        print(f"  {reason:20s}: {cnt}")

    # ── 5. 게이트 체크 ────────────────────────────────────────────────────
    print("\n게이트 체크:")
    gates = {
        "Trades ≥ 100":            m["total_trades"]    >= 100,
        "Win rate ≥ 55%":          m["win_rate"]         >= 0.55,
        "Profit Factor ≥ 1.3":     m["profit_factor"]    >= 1.3,
        "MDD ≥ -15%":              m["max_drawdown_pct"] >= -15,
        "Sharpe ≥ 0.8":            m["sharpe"]           >= 0.8,
        "Tail ratio ≥ 1.0":        extra["tail_ratio"]   >= 1.0,
        "Max losing streak < 6":   extra["max_losing_streak"] < 6,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")

    all_passed = all(gates.values())
    if all_passed:
        print("\n✅ 게이트 통과 — Phase B (민감도 분석) 진행 가능")
    else:
        print("\n❌ 게이트 미달 — 파라미터 조정 필요")
        if m["win_rate"] < 0.55:
            print("   → ibs_threshold 낮추기(0.20) 또는 five_day_low_bars 늘리기(7) 시도")
        if m["profit_factor"] < 1.3:
            print("   → stop_atr_mult 높이기(2.0) 또는 max_hold_days 줄이기(3) 시도")
        if extra["tail_ratio"] < 1.0:
            print("   → 손실 분포가 치우침 — 필터 강화 필요 (IBS < 0.20)")
        if extra["max_losing_streak"] >= 6:
            print("   → 연속 손절 과다 — 레짐 필터 강화 (VIX 임계값 추가 등)")


if __name__ == "__main__":
    main()
