"""
강환국 소성벨마 백테스트.

사전 조건:
  - data/raw/kr/dart_panel/ 에 연도별 parquet (2015-2024) 필요
  - 18년 백테스트 시 yfinance 폴백 활성 (start < 2015 자동)

사용:
  python run_kw_soseongbelma.py --start 2014-01-01     # 12년
  python run_kw_soseongbelma.py --start 2008-01-01     # 18년 (yfinance)
  python run_kw_soseongbelma.py --small-cap-pct 0.15 --top-n 18
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
from src.strategy.kw_soseongbelma import generate_soseongbelma_signals, DART_PANEL_DIR


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2014-01-01")
    p.add_argument("--end",   default=None)
    p.add_argument("--small-cap-pct", type=float, default=None)
    p.add_argument("--top-n",         type=int,   default=None)
    p.add_argument("--w-value",  type=float, default=None)
    p.add_argument("--w-growth", type=float, default=None)
    p.add_argument("--w-margin", type=float, default=None)
    p.add_argument("--refresh",  action="store_true")
    args = p.parse_args()

    profile = get_profile("kr")
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["market"] = {"code": profile.code, "regime_index": profile.index_ticker, "currency": profile.currency}
    cfg["backtest"]["initial_capital_usd"] = cfg["backtest"].get("initial_capital_krw", 50_000_000)
    cap = cfg["backtest"]["initial_capital_usd"]

    params = dict(cfg["kw_soseongbelma"])
    if args.small_cap_pct is not None:
        params["small_cap_pct"] = args.small_cap_pct
    if args.top_n is not None:
        params["top_n"] = args.top_n
    if args.w_value is not None or args.w_growth is not None or args.w_margin is not None:
        params["factor_weights"] = {
            "value":  args.w_value  if args.w_value  is not None else params["factor_weights"]["value"],
            "growth": args.w_growth if args.w_growth is not None else params["factor_weights"]["growth"],
            "margin": args.w_margin if args.w_margin is not None else params["factor_weights"]["margin"],
        }

    start = args.start
    end   = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")

    print(f"[KW 소성벨마] {start} ~ {end}")
    print(f"  params: small_cap_pct={params['small_cap_pct']}, top_n={params['top_n']}")
    print(f"  weights: {params['factor_weights']}")

    # DART panel 가용성 확인
    avail = sorted(int(f.stem) for f in DART_PANEL_DIR.glob("*.parquet"))
    print(f"  DART panel: {avail} ({len(avail)}개 연도)")
    if not avail:
        print("[FAIL] DART panel 없음 — scripts/fetch_dart_kospi_all.py 먼저 실행")
        return

    # 데이터 로드
    t0 = time.time()
    tickers = [profile.index_ticker] + get_kospi_all_tickers()
    price_data = fetch_all(tickers, start, end, min_bars=120, market="kr", refresh=args.refresh)
    print(f"  Price: {len(price_data)} loaded in {time.time()-t0:.1f}s")

    raw_rebal = rebalance_dates_kr_quarterly(start, end, [5, 8, 11, 4], [16, 16, 16, 1])
    calendar  = price_data[profile.index_ticker].index
    rebal_dates = adjust_signals_to_trading(raw_rebal, calendar)
    t0 = time.time()
    panel = build_fundamentals_panel(rebal_dates)
    print(f"  Fundamentals: {len(panel)} tickers in {time.time()-t0:.1f}s")

    # 신호
    t0 = time.time()
    sigs = generate_soseongbelma_signals(panel, price_data, params, start, end)
    print(f"  Signals: {len(sigs)} in {time.time()-t0:.1f}s")
    if not sigs:
        print("[FAIL] 신호 0개 — DART panel year 가 신호일 cover 안 함? 또는 universe 조건 미달")
        return

    # 백테스트
    t0 = time.time()
    eq, trades = run_quarterly_backtest(sigs, price_data, cfg, market="kr")
    print(f"  Backtest: {len(trades)} trades in {time.time()-t0:.1f}s")

    # 메트릭
    m = compute_quarterly_metrics(eq, cap)
    bench_c = price_data[profile.index_ticker]["close"]
    n_years = len(bench_c) / 252
    bench_cagr = ((bench_c.iloc[-1] / bench_c.iloc[0]) ** (1/n_years) - 1) * 100 if n_years > 0 else 0
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
    gates = [
        ("CAGR ≥ 15%",   m["cagr_pct"] >= 15),
        ("MDD ≥ -45%",   m["max_drawdown_pct"] >= -45),
        ("Sharpe ≥ 0.7", m["sharpe"] >= 0.7),
        ("Calmar ≥ 0.4", m["calmar"] >= 0.4),
        ("alpha ≥ +3%p", alpha >= 3),
    ]
    n_pass = sum(1 for _, ok in gates if ok)
    print("\n=== 게이트 ===")
    for g, ok in gates:
        print(f"  {'OK' if ok else 'FAIL'} {g}")
    print(f"\n{n_pass}/{len(gates)} {'[PASS]' if n_pass == len(gates) else '[FAIL]'}")

    # KW SV 와 비교
    sv_path = Path("backtest_results/kr/kw_super_value_metrics.json")
    if sv_path.exists():
        sv_m = json.loads(sv_path.read_text(encoding="utf-8"))
        print(f"\n=== KW Super Value 비교 ===")
        print(f"  CAGR:    {sv_m['cagr_pct']:+.2f}% → 소성벨마 {m['cagr_pct']:+.2f}% ({m['cagr_pct'] - sv_m['cagr_pct']:+.2f}%p)")
        print(f"  Sharpe:  {sv_m['sharpe']:.2f} → 소성벨마 {m['sharpe']:.2f} ({m['sharpe'] - sv_m['sharpe']:+.2f})")
        print(f"  MDD:     {sv_m['max_drawdown_pct']:+.2f}% → 소성벨마 {m['max_drawdown_pct']:+.2f}%")

    # 저장
    out_dir = Path("backtest_results/kr")
    out_dir.mkdir(parents=True, exist_ok=True)
    eq_df = pd.DataFrame({"date": eq.index, "equity": eq.values}).set_index("date")
    eq_df.to_csv(out_dir / "kw_soseongbelma_equity.csv")
    pd.DataFrame(trades).to_csv(out_dir / "kw_soseongbelma_trades.csv", index=False)
    Path(out_dir / "kw_soseongbelma_metrics.json").write_text(
        json.dumps({**m, "alpha_pct": alpha, "kospi_cagr_pct": bench_cagr, "params": params},
                   indent=2, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
