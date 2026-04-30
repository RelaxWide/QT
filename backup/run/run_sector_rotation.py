"""
Sector ETF Rotation 백테스트 (11 SPDR 섹터)
사용법: python run_sector_rotation.py [--refresh]
"""
import argparse
from pathlib import Path
import numpy as np
import yaml

from src.fetch.prices import fetch_all
from src.strategy.sector_rotation import generate_sector_signals, SECTOR_ETFS
from src.backtest.etf_rotation_engine import compute_rotation_metrics


def run_sector_backtest(signals, price_data, cfg):
    """섹터 로테이션 — top_n 동일비중 보유, 월간 리밸런싱."""
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    slippage        = cfg["risk"]["slippage_pct"] / 100

    def _next_date(after):
        ref = next(iter(price_data.values()))
        fut = ref.index[ref.index > after]
        return fut[0] if len(fut) else None

    exec_map = {}
    for sig in signals:
        ed = _next_date(sig.date)
        if ed:
            exec_map[ed] = sig

    all_dates_set = set()
    for df in price_data.values():
        all_dates_set.update(df.index.tolist())
    all_dates = sorted(all_dates_set)

    cash     = float(initial_capital)
    holdings : dict[str, float] = {}
    equity_records = []

    for date in all_dates:
        if date in exec_map:
            sig = exec_map[date]
            for sym, sh in holdings.items():
                df_sym = price_data.get(sym)
                if df_sym is not None and date in df_sym.index:
                    cash += df_sym.loc[date, "open"] * (1 - slippage) * sh
            holdings = {}

            n = len(sig.top_assets)
            alloc_each = cash / n if n > 0 else 0
            for sym in sig.top_assets:
                df_sym = price_data.get(sym)
                if df_sym is None or date not in df_sym.index:
                    continue
                buy_px = df_sym.loc[date, "open"] * (1 + slippage)
                shares = alloc_each / buy_px
                holdings[sym] = shares
                cash -= buy_px * shares

        pos_value = sum(
            price_data[s].loc[date, "close"] * sh
            for s, sh in holdings.items()
            if s in price_data and date in price_data[s].index
        )
        equity_records.append({"date": date, "equity": cash + pos_value})

    import pandas as pd
    equity_curve = pd.DataFrame(equity_records).set_index("date")["equity"]
    return equity_curve


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cfg   = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    sr_p  = cfg["sector_rotation"]
    start = cfg["data"]["start_date"]
    end   = cfg["data"]["end_date"]
    cap   = cfg["backtest"]["initial_capital_usd"]

    universe = sr_p["universe"]
    print(f"Loading sector ETFs: {universe}...")
    price_data = fetch_all(universe, start, end, min_bars=30, refresh=args.refresh)
    print(f"  {len(price_data)} ETFs loaded")

    signals = generate_sector_signals(
        price_data, universe,
        lookback_months=sr_p["lookback_months"],
        top_n=sr_p["top_n"],
    )
    print(f"  {len(signals)} monthly signals")

    from collections import Counter
    all_picks = []
    for s in signals:
        all_picks.extend(s.top_assets)
    print("  Sector selection (top picks):")
    for sym, cnt in Counter(all_picks).most_common():
        print(f"    {sym}: {cnt}회")

    equity = run_sector_backtest(signals, price_data, cfg)
    m      = compute_rotation_metrics(equity, cap)

    print("\n── Sector ETF Rotation Results ───────────────────────")
    for k, v in m.items():
        print(f"  {k:30s}: {v}")

    # SPY 비교
    spy_c    = price_data.get("SPY")
    if spy_c is None:
        spy_data = fetch_all(["SPY"], start, end, min_bars=30)
        spy_c    = spy_data["SPY"]["close"]
    else:
        spy_c = spy_c["close"]
    n_years  = len(equity) / 252
    spy_cagr = ((spy_c.iloc[-1] / spy_c.iloc[0]) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
    spy_ret  = (spy_c.iloc[-1] / spy_c.iloc[0] - 1) * 100
    print(f"\nSPY Buy&Hold 비교:")
    print(f"  SPY 총 수익률: {spy_ret:.2f}%  |  CAGR: {spy_cagr:.2f}%")
    print(f"  전략 총 수익률: {m['total_return_pct']:.2f}%  |  CAGR: {m['cagr_pct']:.2f}%")

    print("\n게이트 체크 [Type 2 — Sector Rotation]:")
    gates = {
        "CAGR ≥ 10%":        m["cagr_pct"]         >= 10.0,
        "MDD ≥ -25%":        m["max_drawdown_pct"]  >= -25,
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
