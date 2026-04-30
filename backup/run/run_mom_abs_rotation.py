"""
Momentum + Absolute Filter Rotation (SPY/QQQ/GLD/IEF)
EEM 제거, 절대모멘텀 음수면 SHY(현금) 보유
사용법: python run_mom_abs_rotation.py [--refresh]
"""
import argparse
from pathlib import Path
import yaml

from src.fetch.prices import fetch_all
from src.backtest.etf_rotation_engine import run_etf_rotation_backtest, compute_rotation_metrics
from src.strategy.etf_rotation import RotationSignal


def generate_mom_abs_signals(price_data, universe, cash_proxy, lookback_months, top_n):
    import pandas as pd
    monthly = {}
    for sym in universe + [cash_proxy]:
        if sym in price_data:
            monthly[sym] = price_data[sym]["close"].resample("ME").last()

    combined = pd.DataFrame({s: monthly[s] for s in universe if s in monthly}).dropna(how="all")
    mom = combined.pct_change(lookback_months)

    signals = []
    for i in range(lookback_months, len(combined)):
        date    = combined.index[i]
        row_mom = mom.iloc[i]
        valid   = {s: row_mom[s] for s in universe if s in row_mom and not pd.isna(row_mom[s])}
        if not valid:
            continue

        top = sorted(valid, key=lambda s: valid[s], reverse=True)[:top_n]
        # 절대모멘텀: 최고 자산의 수익률이 0 미만이면 현금
        top_ret = valid[top[0]] if top else -1
        asset = top[0] if top_ret > 0 else cash_proxy

        signals.append(RotationSignal(date=date, top_asset=asset, returns=valid))
    return signals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    ma_p  = cfg["mom_abs_rotation"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]

    universe     = ma_p["universe"]
    cash_proxy   = ma_p["cash_proxy"]
    tickers      = universe + [cash_proxy]

    print(f"Loading: {tickers}...")
    price_data = fetch_all(tickers, start, end, min_bars=15, refresh=args.refresh)
    print(f"  {len(price_data)} symbols loaded")

    signals = generate_mom_abs_signals(
        price_data, universe, cash_proxy,
        ma_p["lookback_months"], ma_p["top_n"]
    )
    print(f"  {len(signals)} monthly signals")

    from collections import Counter
    picks = Counter(s.top_asset for s in signals)
    print("  Holdings:")
    for sym, cnt in picks.most_common():
        print(f"    {sym}: {cnt}회 ({cnt/len(signals)*100:.1f}%)")

    equity = run_etf_rotation_backtest(signals, price_data, cfg)
    m      = compute_rotation_metrics(equity, cap)

    print("\n── Momentum+Absolute Rotation Results ───────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    spy_c   = price_data["SPY"]["close"]
    n_years = len(equity) / 252
    spy_cagr = ((spy_c.iloc[-1] / spy_c.iloc[0]) ** (1 / n_years) - 1) * 100
    print(f"\nSPY CAGR: {spy_cagr:.2f}%  vs  전략 CAGR: {m['cagr_pct']:.2f}%")

    print("\n게이트 체크:")
    gates = {
        "CAGR ≥ 10%":        m["cagr_pct"]         >= 10.0,
        "MDD ≥ -20%":        m["max_drawdown_pct"]  >= -20,
        "Sharpe ≥ 0.7":      m["sharpe"]            >= 0.7,
        "Monthly WR ≥ 55%":  m["monthly_win_rate"]  >= 0.55,
    }
    for desc, passed in gates.items():
        print(f"  {'✅' if passed else '❌'} {desc}")
    if all(gates.values()):
        print("\n✅ 전 게이트 통과")
    else:
        print(f"\n❌ 미달: {', '.join(d for d, p in gates.items() if not p)}")


if __name__ == "__main__":
    main()
