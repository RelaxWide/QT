"""
DART 강화 Super Quality 백테스트.

사전 조건:
    python scripts/fetch_dart_kospi_all.py --start 2013 --end 2024
    → data/raw/kr/dart_panel/{year}.parquet 완료

사용:
    python run_kw_quality_dart.py --start 2014-01-01
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

from src.fetch.universe import get_kospi_all_tickers
from src.fetch.prices import fetch_all
from src.fetch.fundamentals_kr import build_fundamentals_panel
from src.backtest.quarterly_engine import run_quarterly_backtest, compute_quarterly_metrics
from src.markets import get_profile
from src.strategy._kw_common import rebalance_dates_kr_quarterly, adjust_signals_to_trading
from src.strategy.kw_super_quality_dart import generate_super_quality_dart_signals


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2014-01-01")
    p.add_argument("--end",   default=None)
    p.add_argument("--small-cap-pct", type=float, default=0.20)
    p.add_argument("--top-n",         type=int,   default=15)
    p.add_argument("--w-roe", type=float, default=1.0)
    p.add_argument("--w-gpa", type=float, default=1.0)
    p.add_argument("--w-vol", type=float, default=1.0)
    args = p.parse_args()

    profile = get_profile("kr")
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["market"] = {"code":profile.code,"regime_index":profile.index_ticker,"currency":profile.currency}
    cfg["backtest"]["initial_capital_usd"] = cfg["backtest"].get("initial_capital_krw", 50_000_000)
    cap = cfg["backtest"]["initial_capital_usd"]

    params = {
        "rebalance_months": [5, 8, 11, 4],
        "rebalance_dom":    [16, 16, 16, 1],
        "small_cap_pct":    args.small_cap_pct,
        "top_n":            args.top_n,
        "vol_lookback_days": 120,
        "min_roe": 0.0,
        "min_gpa": 0.0,
        "factor_weights": {"roe": args.w_roe, "gpa": args.w_gpa, "vol": args.w_vol},
    }

    start = args.start
    end   = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")

    print(f"[KW Super Quality DART] {start} ~ {end}")
    print(f"  params: small_cap={args.small_cap_pct} top_n={args.top_n} w=(roe:{args.w_roe}, gpa:{args.w_gpa}, vol:{args.w_vol})")

    # 데이터 로드
    t0 = time.time()
    tickers = [profile.index_ticker] + get_kospi_all_tickers()
    price_data = fetch_all(tickers, start, end, min_bars=120, market="kr")
    print(f"  Price: {len(price_data)} loaded in {time.time()-t0:.1f}s")

    raw_rebal = rebalance_dates_kr_quarterly(start, end, [5,8,11,4], [16,16,16,1])
    calendar  = price_data[profile.index_ticker].index
    rebal_dates = adjust_signals_to_trading(raw_rebal, calendar)
    t0 = time.time()
    panel = build_fundamentals_panel(rebal_dates)
    print(f"  Fundamentals: {len(panel)} in {time.time()-t0:.1f}s")

    # DART panel 가용성 확인
    from src.strategy.kw_super_quality_dart import DART_PANEL_DIR
    avail_years = sorted(int(p.stem) for p in DART_PANEL_DIR.glob("*.parquet"))
    print(f"  DART panel: {avail_years} ({len(avail_years)}개 연도)")
    if not avail_years:
        print("[FAIL] DART panel 없음. 먼저 'python scripts/fetch_dart_kospi_all.py' 실행 필요.")
        return

    # 신호 생성
    t0 = time.time()
    sigs = generate_super_quality_dart_signals(panel, price_data, params, start, end)
    print(f"  Signals: {len(sigs)} in {time.time()-t0:.1f}s")
    if not sigs:
        print("[FAIL] 신호 0개. 확인 사항: DART panel 의 gp_a/roe 컬럼 존재 여부.")
        return

    # 백테스트
    t0 = time.time()
    eq, trades = run_quarterly_backtest(sigs, price_data, cfg, market="kr")
    print(f"  Backtest: {len(trades)} trades in {time.time()-t0:.1f}s")

    m = compute_quarterly_metrics(eq, cap)
    bench_c = price_data[profile.index_ticker]["close"]
    n_years = len(bench_c) / 252
    bench_cagr = ((bench_c.iloc[-1]/bench_c.iloc[0])**(1/n_years) - 1) * 100 if n_years > 0 else 0
    alpha = m["cagr_pct"] - bench_cagr

    print()
    print("=" * 60)
    print(f"  CAGR:    {m['cagr_pct']:+.2f}%")
    print(f"  MDD:     {m['max_drawdown_pct']:+.2f}%")
    print(f"  Sharpe:  {m['sharpe']:.2f}")
    print(f"  Sortino: {m['sortino']:.2f}")
    print(f"  Calmar:  {m['calmar']:.2f}")
    print(f"  Monthly WR: {m['monthly_win_rate']:.2%}")
    print(f"  KOSPI CAGR: {bench_cagr:+.2f}% / alpha: {alpha:+.2f}%p")
    print("=" * 60)

    # 게이트
    print("\n=== 게이트 ===")
    gates = [
        ("CAGR ≥ 15%",   m["cagr_pct"] >= 15),
        ("MDD ≥ -45%",   m["max_drawdown_pct"] >= -45),
        ("Sharpe ≥ 0.7", m["sharpe"] >= 0.7),
        ("Calmar ≥ 0.4", m["calmar"] >= 0.4),
        ("alpha ≥ +3%p", alpha >= 3),
    ]
    n_pass = sum(1 for _, ok in gates if ok)
    for g, ok in gates:
        print(f"  {'✅' if ok else '❌'} {g}")
    print(f"\n{n_pass}/{len(gates)} {'[PASS]' if n_pass == len(gates) else '[FAIL]'}")

    # PyKRX-only 버전과 비교
    pq_path = Path("backtest_results/kr/kw_super_quality_metrics.json")
    if pq_path.exists():
        pq_m = json.loads(pq_path.read_text(encoding="utf-8"))
        print(f"\n=== PyKRX-only Super Quality 비교 ===")
        print(f"  CAGR:    {pq_m['cagr_pct']:+.2f}% → DART {m['cagr_pct']:+.2f}% ({m['cagr_pct'] - pq_m['cagr_pct']:+.2f}%p)")
        print(f"  Sharpe:  {pq_m['sharpe']:.2f} → DART {m['sharpe']:.2f} ({m['sharpe'] - pq_m['sharpe']:+.2f})")
        print(f"  MDD:     {pq_m['max_drawdown_pct']:+.2f}% → DART {m['max_drawdown_pct']:+.2f}%")

    # 저장
    out_dir = Path("backtest_results/kr")
    out_dir.mkdir(parents=True, exist_ok=True)
    eq_df = pd.DataFrame({"date": eq.index, "equity": eq.values}).set_index("date")
    eq_df.to_csv(out_dir / "kw_super_quality_dart_equity.csv")
    pd.DataFrame(trades).to_csv(out_dir / "kw_super_quality_dart_trades.csv", index=False)
    Path(out_dir / "kw_super_quality_dart_metrics.json").write_text(
        json.dumps({**m, "alpha_pct": alpha, "kospi_cagr_pct": bench_cagr, "params": params},
                   indent=2, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
