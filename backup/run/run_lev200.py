"""
Leveraged 200MA 백테스트 (paper trading 통합 보류 — 백테스트 검증 전용)
"""
import argparse
import time
from pathlib import Path
import yaml

from src.fetch.prices import fetch_all
from src.backtest.lev200_engine import run_lev200_backtest
from src.backtest.qqq_225ma_engine import compute_metrics


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]
    s_cfg = cfg.get("leveraged_200ma_strategy", {})
    sig_sym = s_cfg.get("signal_symbol", "SPY")
    lev_sym = s_cfg.get("leveraged_symbol", "UPRO")

    print(f"Loading {sig_sym}, {lev_sym}...")
    t0 = time.time()
    data = fetch_all([sig_sym, lev_sym], start, end, min_bars=210, refresh=args.refresh)
    print(f"  Done in {time.time()-t0:.1f}s ({len(data)} loaded)")

    if lev_sym not in data:
        print(f"❌ {lev_sym} 데이터 없음")
        return

    equity, trades = run_lev200_backtest(data, cfg)
    m = compute_metrics(equity, trades, cap)

    print(f"\n── Leveraged 200MA ({sig_sym} signal, {lev_sym} hold) ──")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    if sig_sym in data:
        sp = data[sig_sym]["close"]
        n_yr = len(sp) / 252
        bh_cagr = ((sp.iloc[-1] / sp.iloc[0]) ** (1/n_yr) - 1) * 100
        print(f"\n  {sig_sym} B&H CAGR     : {bh_cagr:.2f}%")
    if lev_sym in data:
        lev = data[lev_sym]["close"]
        n_yr = len(lev) / 252
        lev_cagr = ((lev.iloc[-1] / lev.iloc[0]) ** (1/n_yr) - 1) * 100
        print(f"  {lev_sym} B&H CAGR     : {lev_cagr:.2f}%")

    print("\n게이트 체크:")
    gates = {
        "CAGR ≥ 20%":     m["cagr_pct"]         >= 20.0,
        "MDD ≥ -45%":     m["max_drawdown_pct"]  >= -45,
        "Sharpe ≥ 0.7":   m["sharpe"]            >= 0.7,
        "거래 ≤ 10/년":   m["n_trades"] / max(len(equity)/252, 1) <= 10,
    }
    for d, ok in gates.items():
        print(f"  {'✅' if ok else '❌'} {d}")
    if all(gates.values()):
        print("\n✅ 전 게이트 통과 (paper trading 통합은 보류 결정)")


if __name__ == "__main__":
    main()
