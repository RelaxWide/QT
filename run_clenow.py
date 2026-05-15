"""
Clenow Stocks on the Move 백테스트

사용:
    python run_clenow.py                                # US 기본 (S&P 500)
    python run_clenow.py --market kr                    # KR (KOSPI200)
    python run_clenow.py --tickers AAPL MSFT --refresh  # 특정 티커
"""
import argparse
import time
from pathlib import Path

import yaml

from src.fetch.universe import get_sp500_tickers, get_kospi200_tickers
from src.fetch.prices import fetch_all
from src.backtest.clenow_engine import run_clenow_backtest, compute_clenow_metrics
from src.markets import get_profile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--market", choices=["us", "kr"], default="us",
                        help="시장 선택 (기본 us)")
    args = parser.parse_args()

    profile = get_profile(args.market)
    cfg     = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    # 시장 메타데이터 cfg 주입
    cfg["market"] = {
        "code":         profile.code,
        "regime_index": profile.index_ticker,
        "currency":     profile.currency,
    }
    # 전략 파라미터에 시장별 기본값 주입 (config.yaml 미설정 시)
    cl_p = cfg.setdefault("clenow_strategy", {})
    cl_p.setdefault("min_price", profile.min_price)
    cl_p.setdefault("index_ticker", profile.index_ticker)

    # 통화별 초기 자본 — 엔진은 initial_capital_usd 키를 통화 무관 수치로 사용
    if args.market == "kr":
        cfg["backtest"]["initial_capital_usd"] = cfg["backtest"].get(
            "initial_capital_krw", 50_000_000
        )

    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]

    # 티커 목록
    if args.tickers:
        tickers = list(args.tickers)
    else:
        tickers = get_kospi200_tickers() if args.market == "kr" else get_sp500_tickers()

    benchmark = profile.index_ticker
    if benchmark not in tickers:
        tickers = [benchmark] + list(tickers)

    min_bars = cl_p["reg_lookback"] + cl_p["ma100_period"] + 10
    print(f"[{profile.name}] Loading price data ({len(tickers)} tickers)...")
    t0 = time.time()
    price_data = fetch_all(tickers, start, end, min_bars=min_bars,
                           refresh=args.refresh, market=args.market)
    print(f"  {len(price_data)} symbols loaded in {time.time()-t0:.1f}s")

    if benchmark not in price_data:
        print(f"❌ {benchmark} 데이터 없음")
        return

    print("Running Clenow backtest (weekly rebalancing)...")
    t0 = time.time()
    equity, rebal_log = run_clenow_backtest(price_data, cfg)
    print(f"  Done in {time.time()-t0:.1f}s")

    m = compute_clenow_metrics(equity, cap)

    out_dir = Path("backtest_results") / args.market
    out_dir.mkdir(parents=True, exist_ok=True)
    equity.to_frame("equity").to_csv(out_dir / "clenow_equity.csv", index_label="date")

    print(f"\n── [{profile.name}] Clenow Stocks on the Move Results ────────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    if rebal_log:
        avg_held = sum(r["n_held"] for r in rebal_log) / len(rebal_log)
        regime_pct = sum(1 for r in rebal_log if r["regime"]) / len(rebal_log) * 100
        print(f"  {'avg_positions_held':30s}: {avg_held:.1f}")
        print(f"  {'regime_in_pct':30s}: {regime_pct:.1f}%")

    # 벤치마크 비교
    bench_c   = price_data[benchmark]["close"]
    bench_ret = (bench_c.iloc[-1] / bench_c.iloc[0] - 1) * 100
    n_years   = len(equity) / 252
    bench_cagr = ((bench_c.iloc[-1] / bench_c.iloc[0]) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
    print(f"\n{benchmark} Buy&Hold 비교:")
    print(f"  {benchmark} 총 수익률: {bench_ret:.2f}%  |  CAGR: {bench_cagr:.2f}%")
    print(f"  전략 총 수익률: {m['total_return_pct']:.2f}%  |  CAGR: {m['cagr_pct']:.2f}%")

    # 게이트 (시장별 완화 적용 — KR 은 모멘텀 약화 가능성 고려)
    if args.market == "kr":
        gates = {
            "CAGR ≥ 8%":         m["cagr_pct"]         >= 8.0,
            "MDD ≥ -25%":        m["max_drawdown_pct"]  >= -25,
            "Sharpe ≥ 0.6":      m["sharpe"]            >= 0.6,
            "Monthly WR ≥ 50%":  m["monthly_win_rate"]  >= 0.50,
        }
        gate_label = "KR 완화 게이트"
    else:
        gates = {
            "CAGR ≥ 10%":        m["cagr_pct"]         >= 10.0,
            "MDD ≥ -25%":        m["max_drawdown_pct"]  >= -25,
            "Sharpe ≥ 0.7":      m["sharpe"]            >= 0.7,
            "Monthly WR ≥ 55%":  m["monthly_win_rate"]  >= 0.55,
        }
        gate_label = "Type 3 - Trend/Momentum"

    print(f"\n게이트 체크 [{gate_label}]:")
    for desc, passed in gates.items():
        print(f"  {'OK' if passed else 'FAIL'} {desc}")

    if all(gates.values()):
        print("\n[PASS] 전 게이트 통과")
    else:
        failed = [d for d, p in gates.items() if not p]
        print(f"\n[FAIL] 미달 항목: {', '.join(failed)}")


if __name__ == "__main__":
    main()
