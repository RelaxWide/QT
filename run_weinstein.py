"""
Weinstein Stage 2 백테스트

사용:
    python run_weinstein.py                  # US (S&P 500)
    python run_weinstein.py --market kr      # KR (KOSPI200, W-FRI 주봉)
"""
import argparse
import time
from pathlib import Path

import yaml

from src.fetch.universe import get_sp500_tickers, get_kospi200_tickers
from src.fetch.prices import fetch_all
from src.strategy.weinstein_stage2 import generate_weinstein_signals
from src.backtest.weinstein_engine import run_weinstein_backtest
from src.backtest.metrics import compute_metrics, save_report, compute_rotation_metrics
from src.markets import get_profile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--market", choices=["us", "kr"], default="us")
    args = parser.parse_args()

    profile = get_profile(args.market)
    cfg     = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    cfg["market"] = {
        "code":         profile.code,
        "regime_index": profile.index_ticker,
        "currency":     profile.currency,
    }

    w_p = cfg["weinstein_strategy"]
    w_p.setdefault("min_price",   profile.min_price)
    w_p.setdefault("weekly_freq", profile.calendar_freq)

    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]

    if args.tickers:
        tickers = list(args.tickers)
    else:
        tickers = get_kospi200_tickers() if args.market == "kr" else get_sp500_tickers()

    benchmark = profile.index_ticker
    if benchmark not in tickers:
        tickers = [benchmark] + list(tickers)

    min_bars = w_p["ma30_period"] * 5 + 10
    print(f"[{profile.name}] Loading price data ({len(tickers)} tickers)...")
    t0 = time.time()
    price_data = fetch_all(tickers, start, end, min_bars=min_bars,
                           refresh=args.refresh, market=args.market)
    print(f"  {len(price_data)} symbols loaded in {time.time()-t0:.1f}s")

    if benchmark not in price_data:
        print(f"❌ {benchmark} 없음"); return

    print("Generating Weinstein Stage 2 signals...")
    all_signals = []
    for sym, df in price_data.items():
        if sym == benchmark:
            continue
        sigs = generate_weinstein_signals(sym, df, w_p)
        all_signals.extend(sigs)
    all_signals.sort(key=lambda s: s.entry_date)
    print(f"  {len(all_signals)} signals")

    if not all_signals:
        print("⚠️  시그널 없음"); return

    print("Running Weinstein backtest...")
    bench_close = price_data[benchmark]["close"]
    result = run_weinstein_backtest(all_signals, price_data, bench_close, cfg)
    print(f"  {len(result.trades)} trades closed")

    m = compute_metrics(result)
    out_dir = Path("backtest_results") / args.market
    out_dir.mkdir(parents=True, exist_ok=True)
    save_report(m, result, output_dir=str(out_dir), prefix="weinstein")
    em = compute_rotation_metrics(result.equity_curve, cap)

    print(f"\n── [{profile.name}] Weinstein Stage 2 Results ─────────────")
    for k, v in m.items():
        if k != "exit_reasons":
            print(f"  {k:30s}: {v}")
    print(f"\n  {'cagr_pct':30s}: {em['cagr_pct']}")
    print(f"  {'monthly_win_rate':30s}: {em['monthly_win_rate']}")

    print("\n청산 사유:")
    for reason, cnt in m.get("exit_reasons", {}).items():
        print(f"  {reason:20s}: {cnt}")

    if args.market == "kr":
        gates = {
            "Trades >= 50":        m["total_trades"]    >= 50,
            "Profit Factor >= 1.3": m["profit_factor"]  >= 1.3,
            "MDD >= -30%":         m["max_drawdown_pct"] >= -30,
            "Sharpe >= 0.6":       m["sharpe"]           >= 0.6,
            "CAGR >= 6%":          em["cagr_pct"]        >= 6.0,
        }
    else:
        gates = {
            "Trades >= 100":       m["total_trades"]    >= 100,
            "Profit Factor >= 1.5": m["profit_factor"]  >= 1.5,
            "MDD >= -25%":         m["max_drawdown_pct"] >= -25,
            "Sharpe >= 0.7":       m["sharpe"]           >= 0.7,
            "CAGR >= 8%":          em["cagr_pct"]        >= 8.0,
        }

    print("\n게이트 체크:")
    for desc, passed in gates.items():
        print(f"  {'OK' if passed else 'FAIL'} {desc}")
    if all(gates.values()):
        print("\n[PASS] 전 게이트 통과")
    else:
        print(f"\n[FAIL] 미달: {', '.join(d for d, p in gates.items() if not p)}")


if __name__ == "__main__":
    main()
