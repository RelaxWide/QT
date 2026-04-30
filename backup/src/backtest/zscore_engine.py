"""
Z-score MR 엔진 — 단일 종목, 종가 진입/청산, 손절 ATR 배수
"""
import pandas as pd

from src.strategy.zscore_mr import calc_rsi, calc_atr, calc_zscore
from src.backtest.costs import make_cost_model


def run_zscore_backtest(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, list[dict]]:
    s_cfg = cfg.get("zscore_mr_strategy", {})
    cap   = cfg["backtest"]["initial_capital_usd"]
    slip  = cfg["risk"]["slippage_pct"] / 100
    cost  = make_cost_model(cfg)

    z_period   = s_cfg.get("z_period", 20)
    z_entry    = s_cfg.get("z_entry", -2.0)
    z_exit     = s_cfg.get("z_exit", 0.0)
    rsi_period = s_cfg.get("rsi_period", 14)
    rsi_max    = s_cfg.get("rsi_max", 30)
    atr_period = s_cfg.get("atr_period", 14)
    stop_atr   = s_cfg.get("stop_atr_mult", 3.0)
    max_hold   = s_cfg.get("max_hold_days", 12)
    pos_pct    = s_cfg.get("position_size_pct", 100) / 100

    z   = calc_zscore(df["close"], z_period)
    rsi = calc_rsi(df["close"], rsi_period)
    atr = calc_atr(df, atr_period)

    cash = float(cap)
    shares = 0.0
    entry_px = entry_date = stop_px = None
    hold = 0
    trades = []
    eq = []

    for i, date in enumerate(df.index):
        c = df.loc[date, "close"]
        if shares > 0:
            hold += 1
            zv = z.iloc[i]
            stop_hit = c < stop_px
            exit_now = stop_hit or (not pd.isna(zv) and zv >= z_exit) or hold >= max_hold
            if exit_now:
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
        if shares == 0:
            zv = z.iloc[i]
            rv = rsi.iloc[i]
            av = atr.iloc[i]
            if (not pd.isna(zv) and zv <= z_entry and
                not pd.isna(rv) and rv < rsi_max and
                not pd.isna(av) and av > 0):
                px = c * (1 + slip)
                alloc = cash * pos_pct
                buy_c = cost.buy_cost(alloc)
                shares = (alloc - buy_c) / px
                cash -= shares * px + buy_c
                entry_px = px
                entry_date = date
                stop_px = px - stop_atr * av
                hold = 0
        eq.append({"date": date, "equity": cash + shares * c})

    return pd.DataFrame(eq).set_index("date")["equity"], trades
