"""
Run Meb Faber GTAA-5 style tactical asset allocation backtest.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

from src.backtest.haa_engine import run_haa_backtest
from src.backtest.metrics import compute_rotation_metrics
from src.fetch.prices import fetch_all
from src.strategy.gtaa import generate_gtaa_signals


def _save_outputs(prefix: str, equity: pd.Series, trades: pd.DataFrame, signals, metrics: dict) -> None:
    out = Path("backtest_results")
    out.mkdir(parents=True, exist_ok=True)
    equity.to_csv(out / f"{prefix}_equity.csv", header=True)
    trades.to_csv(out / f"{prefix}_trades.csv", index=False)
    pd.DataFrame(
        [
            {
                "date": sig.date,
                **{f"weight_{sym}": weight for sym, weight in sig.weights.items()},
                **{f"above_{sym}": value for sym, value in sig.above_sma.items()},
            }
            for sig in signals
        ]
    ).to_csv(out / f"{prefix}_signals.csv", index=False)
    lines = ["# GTAA Backtest Results", "", "| Metric | Value |", "|---|---:|"]
    for key, value in metrics.items():
        lines.append(f"| {key} | {value} |")
    (out / f"{prefix}_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _spy_metrics(price_data: dict[str, pd.DataFrame], equity: pd.Series) -> dict:
    if "SPY" not in price_data or equity.empty:
        return {}
    spy = price_data["SPY"]["close"]
    spy = spy[spy.index >= equity.index[0]]
    n_years = len(spy) / 252
    cagr = (spy.iloc[-1] / spy.iloc[0]) ** (1 / n_years) - 1 if n_years > 0 else 0
    dd = (spy - spy.cummax()) / spy.cummax()
    ret = spy.pct_change().dropna()
    sharpe = ret.mean() / ret.std() * (252 ** 0.5) if ret.std() > 0 else 0
    return {
        "spy_cagr_pct": round(cagr * 100, 2),
        "spy_mdd_pct": round(float(dd.min()) * 100, 2),
        "spy_sharpe": round(float(sharpe), 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--tag", default="default")
    parser.add_argument("--assets", default="SPY,EFA,VNQ,IEF,DBC")
    parser.add_argument("--cash-proxy", default="BIL")
    parser.add_argument("--sma-months", type=int, default=10)
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    assets = [sym.strip().upper() for sym in args.assets.split(",") if sym.strip()]
    tickers = sorted(set(assets + [args.cash_proxy, "SPY"]))
    data_cfg = cfg["data"]
    print(f"Loading GTAA ETFs: {tickers}")
    price_data = fetch_all(
        tickers,
        data_cfg["start_date"],
        data_cfg.get("end_date"),
        min_bars=260,
        refresh=args.refresh,
        cache_dir=data_cfg.get("cache_dir"),
    )

    signals = generate_gtaa_signals(price_data, assets, args.cash_proxy, args.sma_months)
    print(f"Generated {len(signals)} monthly GTAA signals")
    if not signals:
        raise SystemExit("No signals generated")

    equity, trades = run_haa_backtest(signals, price_data, cfg)
    metrics = compute_rotation_metrics(equity, cfg["backtest"]["initial_capital_usd"])
    metrics.update(
        {
            "signals": len(signals),
            "trade_events": int(len(trades)),
            "start": str(equity.index[0].date()),
            "end": str(equity.index[-1].date()),
            "assets": ",".join(assets),
            "cash_proxy": args.cash_proxy,
            "sma_months": args.sma_months,
        }
    )
    metrics.update(_spy_metrics(price_data, equity))

    prefix = f"gtaa_{args.tag}"
    _save_outputs(prefix, equity, trades, signals, metrics)

    print("\nGTAA Results")
    for key, value in metrics.items():
        print(f"  {key:24s}: {value}")
    print(f"\nReports saved: backtest_results/{prefix}_*.csv/md")

    gates = {
        "CAGR >= 7%": metrics["cagr_pct"] >= 7.0,
        "MDD >= -20%": metrics["max_drawdown_pct"] >= -20.0,
        "Sharpe >= 0.8": metrics["sharpe"] >= 0.8,
    }
    print("\nGates")
    for name, ok in gates.items():
        print(f"  {'PASS' if ok else 'FAIL'} {name}")


if __name__ == "__main__":
    main()
