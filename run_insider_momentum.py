"""
내부자매수 + 200일선 추세필터 백테스트.

사전 조건:
  - data/raw/us_insider/insider_purchases_p.parquet (scripts/parse_insider_transactions.py)
  - data/raw_russell/*.parquet (가격, 이미 캐시됨)

사용:
    python run_insider_momentum.py
    python run_insider_momentum.py --top-n 15 --lookback-months 1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import yaml

from src.backtest.quarterly_engine import run_quarterly_backtest, compute_quarterly_metrics
from src.strategy.insider_momentum import generate_insider_momentum_signals

PRICE_DIR = Path("data/raw_russell")
INSIDER_PATH = Path("data/raw/us_insider/insider_purchases_p.parquet")


def load_price_data(tickers: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for t in tickers:
        p = PRICE_DIR / f"{t}.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        df.index = pd.to_datetime(df.index)
        out[t] = df.sort_index()
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2021-07-01")
    p.add_argument("--end",   default=None)
    p.add_argument("--top-n", type=int, default=10)
    p.add_argument("--lookback-months", type=int, default=3)
    p.add_argument("--ma-window", type=int, default=200)
    args = p.parse_args()

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    cfg["backtest"]["initial_capital_usd"] = cfg["backtest"].get("initial_capital_usd", 100000)
    cap = cfg["backtest"]["initial_capital_usd"]

    params = {"top_n": args.top_n, "lookback_months": args.lookback_months, "ma_window": args.ma_window}

    insider_df = pd.read_parquet(INSIDER_PATH)
    insider_df = insider_df[(insider_df["date"] >= "2021-01-01") & (insider_df["date"] <= "2026-10-31")]
    # SEC 원본 Form4 데이터 자체에 가격/수량 오기입 오류가 흔함 (예: 주당가격을
    # $2900만으로 입력 — 폐쇄형펀드 신주발행 관련 보고 오류로 추정). 1건 거래로
    # 말이 안 되는 금액(>$5억)은 전략 신호에서 배제.
    before = len(insider_df)
    insider_df = insider_df[(insider_df["price"] > 0) & (insider_df["price"] < 100_000)
                             & (insider_df["value_usd"] < 5e8)]
    print(f"[내부자매수] 데이터 오류 제외 {before - len(insider_df)}건")
    print(f"[내부자매수] {len(insider_df)}건, {insider_df['ticker'].nunique()}종목")

    tickers = sorted(set(insider_df["ticker"]) | {"SPY"})
    price_data = load_price_data(tickers)
    print(f"[가격] {len(price_data)}종목 로드")

    end = args.end or price_data["SPY"].index.max().strftime("%Y-%m-%d")
    print(f"[기간] {args.start} ~ {end}")

    sigs = generate_insider_momentum_signals(insider_df, price_data, params, args.start, end)
    print(f"[신호] {len(sigs)}개월")
    if not sigs:
        print("[FAIL] 신호 0개")
        return

    eq, trades = run_quarterly_backtest(sigs, price_data, cfg, market="us")
    print(f"[백테스트] {len(trades)}건 거래")

    m = compute_quarterly_metrics(eq, cap)
    spy = price_data["SPY"]["close"]
    spy_window = spy[(spy.index >= eq.index.min()) & (spy.index <= eq.index.max())]
    n_years = len(eq) / 252
    spy_cagr = ((spy_window.iloc[-1] / spy_window.iloc[0]) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
    alpha = m["cagr_pct"] - spy_cagr

    print()
    print("=" * 60)
    print(f"  CAGR:    {m['cagr_pct']:+.2f}%")
    print(f"  MDD:     {m['max_drawdown_pct']:+.2f}%")
    print(f"  Sharpe:  {m['sharpe']:.2f}")
    print(f"  Sortino: {m['sortino']:.2f}")
    print(f"  Calmar:  {m['calmar']:.2f}")
    print(f"  Monthly WR: {m['monthly_win_rate']:.2%}")
    print(f"  SPY CAGR: {spy_cagr:+.2f}% / alpha: {alpha:+.2f}%p")
    print("=" * 60)

    gates = [
        ("CAGR >= 15%",   m["cagr_pct"] >= 15),
        ("MDD >= -25%",   m["max_drawdown_pct"] >= -25),
        ("Sharpe >= 0.7", m["sharpe"] >= 0.7),
        ("Calmar >= 0.4", m["calmar"] >= 0.4),
        ("alpha >= +3%p", alpha >= 3),
    ]
    n_pass = sum(1 for _, ok in gates if ok)
    print("\n=== 게이트 ===")
    for g, ok in gates:
        print(f"  {'OK' if ok else 'FAIL'} {g}")
    print(f"\n{n_pass}/{len(gates)} {'[PASS]' if n_pass == len(gates) else '[FAIL]'}")

    out_dir = Path("backtest_results/us")
    out_dir.mkdir(parents=True, exist_ok=True)
    eq.to_frame("equity").to_csv(out_dir / "insider_momentum_equity.csv")
    trades.to_csv(out_dir / "insider_momentum_trades.csv", index=False)
    Path(out_dir / "insider_momentum_metrics.json").write_text(
        json.dumps({**m, "alpha_pct": alpha, "spy_cagr_pct": spy_cagr, "params": params},
                   indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
