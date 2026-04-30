"""
Connors 3-Day H/L 엔진 — SPY 단일 종목, 종가 매매
"""
import numpy as np
import pandas as pd

from src.strategy.connors_3day import generate_signals
from src.backtest.costs import make_cost_model


def run_3day_backtest(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, list[dict]]:
    s_cfg    = cfg.get("connors_3day_strategy", {})
    cap      = cfg["backtest"]["initial_capital_usd"]
    slip     = cfg["risk"]["slippage_pct"] / 100
    pos_pct  = s_cfg.get("position_size_pct", 100) / 100
    cost     = make_cost_model(cfg)

    entry_sig, exit_sig = generate_signals(df,
        s_cfg.get("ma_long", 200),
        s_cfg.get("ma_exit", 5))

    cash = float(cap)
    shares = 0.0
    entry_px = None
    entry_date = None
    trades = []
    eq = []

    for i, date in enumerate(df.index):
        c = df.loc[date, "close"]
        if shares > 0 and exit_sig.iloc[i]:
            px = c * (1 - slip)
            sell_c = cost.sell_cost(px * shares)
            pnl = (px - entry_px) * shares - sell_c
            cash += px * shares - sell_c
            trades.append({"entry_date": entry_date, "exit_date": date,
                           "entry_price": entry_px, "exit_price": px,
                           "shares": shares, "pnl": pnl})
            shares = 0.0
            entry_px = None
        if shares == 0 and entry_sig.iloc[i]:
            px = c * (1 + slip)
            alloc = cash * pos_pct
            buy_c = cost.buy_cost(alloc)
            shares = (alloc - buy_c) / px
            cash -= shares * px + buy_c
            entry_px = px
            entry_date = date
        eq.append({"date": date, "equity": cash + shares * c})

    equity = pd.DataFrame(eq).set_index("date")["equity"]
    return equity, trades
