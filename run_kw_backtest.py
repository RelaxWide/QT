"""
KW (강환국 류) 펀더멘털 전략 백테스트 — KR 전용.

사용:
  python run_kw_backtest.py --strategy super_value  --start 2014-01-01
  python run_kw_backtest.py --strategy super_quality --start 2014-01-01
  python run_kw_backtest.py --strategy ultra        --start 2014-01-01
  python run_kw_backtest.py --strategy super_value --small-cap-pct 0.10 --top-n 15

KRX_ID/KRX_PW 환경변수 필요 (PyKRX fundamental 호출용).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.fetch.universe import get_kospi_all_tickers, get_kospi200_tickers
from src.fetch.universe_kr import get_kospi_top_n_tickers
from src.fetch.prices import fetch_all
from src.fetch.fundamentals_kr import build_fundamentals_panel
from src.backtest.quarterly_engine import run_quarterly_backtest, compute_quarterly_metrics
from src.markets import get_profile
from src.strategy._kw_common import rebalance_dates_kr_quarterly


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=["super_value", "super_quality", "ultra"], required=True)
    parser.add_argument("--universe", choices=["kospi_all", "kospi500", "kospi200"],
                        default="kospi_all", help="KR universe (기본 kospi_all)")
    parser.add_argument("--small-cap-pct", type=float, default=None)
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--start", default="2014-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    profile = get_profile("kr")
    cfg     = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["market"] = {
        "code": profile.code, "regime_index": profile.index_ticker,
        "currency": profile.currency,
    }
    # KR 자본
    cfg["backtest"]["initial_capital_usd"] = cfg["backtest"].get("initial_capital_krw", 50_000_000)
    cap = cfg["backtest"]["initial_capital_usd"]

    # 전략 파라미터
    param_key = f"kw_{args.strategy}"
    params = dict(cfg.get(param_key, {}))
    if args.small_cap_pct is not None:
        params["small_cap_pct"] = args.small_cap_pct
    if args.top_n is not None:
        params["top_n"] = args.top_n

    # universe 선택
    if args.universe == "kospi_all":
        tickers = get_kospi_all_tickers()
    elif args.universe == "kospi500":
        tickers = get_kospi_top_n_tickers(500)
    else:
        tickers = get_kospi200_tickers()
    if profile.index_ticker not in tickers:
        tickers = [profile.index_ticker] + tickers

    start = args.start
    end   = args.end or cfg["data"].get("end_date") or pd.Timestamp.today().strftime("%Y-%m-%d")
    if end is None or (isinstance(end, float) and pd.isna(end)):
        end = pd.Timestamp.today().strftime("%Y-%m-%d")

    print(f"[KW {args.strategy}] universe={args.universe} ({len(tickers)} tickers) | start={start}")

    # 1) 가격 데이터
    print("Loading price data ...")
    t0 = time.time()
    price_data = fetch_all(tickers, start, end, min_bars=120,
                           refresh=args.refresh, market="kr")
    print(f"  {len(price_data)} loaded in {time.time()-t0:.1f}s")

    if profile.index_ticker not in price_data:
        print(f"[STOP] {profile.index_ticker} 없음")
        return

    # 2) 펀더멘털 panel — 리밸런싱 일자만 fetch
    rebal_months = params.get("rebalance_months", [5, 8, 11, 4])
    rebal_dom    = params.get("rebalance_dom",    [16, 16, 16, 1])
    rebal_dates  = rebalance_dates_kr_quarterly(start, end, rebal_months, rebal_dom)
    print(f"Rebalancing dates: {len(rebal_dates)}")

    print("Loading fundamentals (per rebalance date) ...")
    t0 = time.time()
    panel = build_fundamentals_panel(rebal_dates, refresh=args.refresh)
    print(f"  panel {len(panel)} tickers in {time.time()-t0:.1f}s")

    if not panel:
        print("[STOP] fundamentals panel 비어있음. KRX_ID/KRX_PW 환경변수 확인.")
        print("       https://data.krx.co.kr/ 회원 가입 후 환경변수 설정")
        return

    # 3) 신호 생성
    print("Generating signals ...")
    t0 = time.time()
    if args.strategy == "super_value":
        from src.strategy.kw_super_value import generate_super_value_signals
        signals = generate_super_value_signals(panel, price_data, params, start, end)
    elif args.strategy == "super_quality":
        from src.strategy.kw_super_quality import generate_super_quality_signals
        signals = generate_super_quality_signals(panel, price_data, params, start, end)
    else:
        from src.strategy.kw_ultra import generate_ultra_signals
        signals = generate_ultra_signals(panel, price_data, params, start, end)
    print(f"  {len(signals)} signals in {time.time()-t0:.1f}s")
    if not signals:
        print("[STOP] 신호 0건. fundamentals 부족.")
        return

    # 4) 백테스트
    print("Running quarterly backtest ...")
    t0 = time.time()
    equity, trades = run_quarterly_backtest(signals, price_data, cfg, market="kr")
    print(f"  Done in {time.time()-t0:.1f}s")

    m = compute_quarterly_metrics(equity, cap)

    out_dir = Path("backtest_results/kr")
    out_dir.mkdir(parents=True, exist_ok=True)
    equity.to_frame("equity").to_csv(out_dir / f"kw_{args.strategy}_equity.csv", index_label="date")
    if not trades.empty:
        trades.to_csv(out_dir / f"kw_{args.strategy}_trades.csv", index=False)
    (out_dir / f"kw_{args.strategy}_metrics.json").write_text(
        json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    bench_c = price_data[profile.index_ticker]["close"]
    bench_ret = (bench_c.iloc[-1] / bench_c.iloc[0] - 1) * 100
    n_years   = len(equity) / 252
    bench_cagr = ((bench_c.iloc[-1] / bench_c.iloc[0]) ** (1/n_years) - 1) * 100 if n_years > 0 else 0
    alpha = m["cagr_pct"] - bench_cagr

    print(f"\n=== [KW {args.strategy}] Results ({start} ~ {pd.Timestamp(equity.index[-1]).date()}) ===")
    for k, v in m.items():
        print(f"  {k:25s}: {v}")
    print(f"  alpha_vs_kospi(pp)       : {alpha:+.2f}")
    print()
    print(f"^KS11 B&H: ret {bench_ret:.2f}% / CAGR {bench_cagr:.2f}%")
    print(f"전략:     ret {m['total_return_pct']:.2f}% / CAGR {m['cagr_pct']:.2f}%")

    # 게이트 (KW 전용 기준)
    gates = {
        "CAGR >= 15%":         m["cagr_pct"]         >= 15.0,
        "MDD >= -45%":         m["max_drawdown_pct"] >= -45,
        "Sharpe >= 0.7":       m["sharpe"]           >= 0.7,
        "Calmar >= 0.4":       m["calmar"]           >= 0.4,
        "alpha >= +3%p":       alpha                 >= 3.0,
    }
    print()
    print("게이트 체크 (KW 기준):")
    for desc, passed in gates.items():
        print(f"  {'OK' if passed else 'FAIL'} {desc}")
    if all(gates.values()):
        print("\n[PASS] 전 게이트 통과")
    else:
        print(f"\n[FAIL] 미달: {', '.join(d for d, p in gates.items() if not p)}")


if __name__ == "__main__":
    main()
