"""
TOM SPY 엔진 — 종가 매매
"""
import numpy as np
import pandas as pd

from src.strategy.tom_spy import tom_signal_dates
from src.backtest.costs import make_cost_model


def run_tom_backtest(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, list[dict]]:
    s_cfg    = cfg.get("tom_strategy", {})
    cap      = cfg["backtest"]["initial_capital_usd"]
    slip     = cfg["risk"]["slippage_pct"] / 100
    n_before = s_cfg.get("days_before_eom", 5)
    n_after  = s_cfg.get("days_after_bom", 3)
    cost     = make_cost_model(cfg)

    entry_set, exit_set = tom_signal_dates(df.index, n_before, n_after)

    cash = float(cap)
    shares = 0.0
    entry_px = None
    entry_date = None
    trades = []
    eq = []

    for date in df.index:
        c = df.loc[date, "close"]
        # exit at close
        if shares > 0 and date in exit_set:
            px = c * (1 - slip)
            sell_c = cost.sell_cost(px * shares)
            pnl = (px - entry_px) * shares - sell_c
            cash += px * shares - sell_c
            trades.append({"entry_date": entry_date, "exit_date": date,
                           "entry_price": entry_px, "exit_price": px,
                           "shares": shares, "pnl": pnl})
            shares = 0.0
            entry_px = None
        # entry at close
        if shares == 0 and date in entry_set:
            px = c * (1 + slip)
            alloc = cash * 0.98
            buy_c = cost.buy_cost(alloc)
            shares = (alloc - buy_c) / px
            cash -= shares * px + buy_c
            entry_px = px
            entry_date = date

        eq.append({"date": date, "equity": cash + shares * c})

    equity = pd.DataFrame(eq).set_index("date")["equity"]
    return equity, trades
