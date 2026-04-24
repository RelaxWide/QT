"""
VIX Mean Reversion (SPY) 백테스트
사용법: python run_vix_mr.py [--refresh]
"""
import argparse
from pathlib import Path
import numpy as np
import yaml

from src.fetch.prices import fetch_all
from src.strategy.vix_mr import generate_vix_signals
from src.backtest.vix_mr_engine import run_vix_mr_backtest
from src.backtest.metrics import compute_metrics, save_report


def compute_extra(result):
    r_mults = [t.r_multiple for t in result.trades]
    if not r_mults:
        return {"tail_ratio": 0.0, "max_losing_streak": 0}
    arr   = np.array(r_mults)
    top5  = np.percentile(arr, 95)
    bot5  = abs(np.percentile(arr, 5))
    tail  = top5 / bot5 if bot5 > 0 else float("inf")
    streak = max_streak = 0
    for r in r_mults:
        streak = streak + 1 if r <= 0 else 0
        max_streak = max(max_streak, streak)
    return {"tail_ratio": round(tail, 4), "max_losing_streak": max_streak}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    vp    = cfg["vix_mr_strategy"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]

    print("Loading SPY and VIX data...")
    price_data = fetch_all(["SPY", "^VIX"], start, end, min_bars=210, refresh=args.refresh)

    if "SPY" not in price_data:
        print("❌ SPY 없음"); return

    spy_df = price_data["SPY"]
    vix_df = price_data.get("^VIX")
    if vix_df is None:
        print("❌ VIX 데이터 없음"); return

    vix_series = vix_df["close"]
    print(f"  VIX range: {vix_series.min():.1f} ~ {vix_series.max():.1f}")

    signals = generate_vix_signals(spy_df, vix_series, vp)
    print(f"  {len(signals)} VIX signals generated")

    if not signals:
        print("⚠️  시그널 없음"); return

    result = run_vix_mr_backtest(signals, spy_df, vix_series, cfg)
    print(f"  {len(result.trades)} trades closed")

    m     = compute_metrics(result)
    extra = compute_extra(result)
    save_report(m, result, output_dir="backtest_results", prefix="vix_mr")

    print("\n── VIX Mean Reversion Results ────────────────────────")
    for k, v in m.items():
        if k != "exit_reasons":
            print(f"  {k:30s}: {v}")
    print(f"  {'tail_ratio':30s}: {extra['tail_ratio']}")
    print(f"  {'max_losing_streak':30s}: {extra['max_losing_streak']}")

    print("\n청산 사유:")
    for reason, cnt in m.get("exit_reasons", {}).items():
        print(f"  {reason:20s}: {cnt}")

    print("\n게이트 체크:")
    gates = {
        "Trades ≥ 20":          m["total_trades"]    >= 20,
        "Win rate ≥ 60%":       m["win_rate"]         >= 0.60,
        "Profit Factor ≥ 1.5":  m["profit_factor"]    >= 1.5,
        "MDD ≥ -15%":           m["max_drawdown_pct"] >= -15,
        "Sharpe ≥ 0.7":         m["sharpe"]           >= 0.7,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")
    if all(gates.values()):
        print("\n✅ 전 게이트 통과")
    else:
        print(f"\n❌ 미달: {', '.join(d for d, p in gates.items() if not p)}")


if __name__ == "__main__":
    main()
