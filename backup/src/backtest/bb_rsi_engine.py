"""
BB+RSI Double 엔진 — 단일 종목 (SPY/QQQ), 종가 매매
"""
import pandas as pd

from src.strategy.bb_rsi_double import generate_signals
from src.backtest.costs import make_cost_model


def run_bb_rsi_backtest(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, list[dict]]:
    s_cfg = cfg.get("bb_rsi_strategy", {})
    cap   = cfg["backtest"]["initial_capital_usd"]
    slip  = cfg["risk"]["slippage_pct"] / 100
    cost  = make_cost_model(cfg)
    pos_pct = s_cfg.get("position_size_pct", 100) / 100
    max_hold = s_cfg.get("max_hold_days", 60)
    stop_pct = s_cfg.get("stop_pct", 0.10)

    entry_sig, exit_sig = generate_signals(df,
        s_cfg.get("bb_period", 200),
        s_cfg.get("bb_std", 2.0),
        s_cfg.get("rsi_period", 6),
        s_cfg.get("rsi_thresh", 50))

    cash = float(cap)
    shares = 0.0
    entry_px = None
    entry_date = None
    hold = 0
    trades = []
    eq = []

    for i, date in enumerate(df.index):
        c = df.loc[date, "close"]
        if shares > 0:
            hold += 1
            stop_hit = c < entry_px * (1 - stop_pct)
            if exit_sig.iloc[i] or hold >= max_hold or stop_hit:
                px = c * (1 - slip)
                sell_c = cost.sell_cost(px * shares)
                pnl = (px - entry_px) * shares - sell_c
                cash += px * shares - sell_c
                trades.append({"entry_date": entry_date, "exit_date": date,
                               "entry_price": entry_px, "exit_price": px,
                               "shares": shares, "pnl": pnl})
                shares = 0.0
                entry_px = None
                hold = 0
        if shares == 0 and entry_sig.iloc[i]:
            px = c * (1 + slip)
            alloc = cash * pos_pct
            buy_c = cost.buy_cost(alloc)
            shares = (alloc - buy_c) / px
            cash -= shares * px + buy_c
            entry_px = px
            entry_date = date
            hold = 0
        eq.append({"date": date, "equity": cash + shares * c})

    return pd.DataFrame(eq).set_index("date")["equity"], trades
