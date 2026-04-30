"""
VIX Term Structure 엔진 — 일별 신호, 다음날 시가 매매
"""
import pandas as pd

from src.strategy.vix_term import vix_term_signal
from src.backtest.costs import make_cost_model


def run_vix_term_backtest(price_data: dict[str, pd.DataFrame], cfg: dict) -> tuple[pd.Series, list[dict]]:
    s_cfg = cfg.get("vix_term_strategy", {})
    cap   = cfg["backtest"]["initial_capital_usd"]
    slip  = cfg["risk"]["slippage_pct"] / 100
    cost  = make_cost_model(cfg)
    sym   = s_cfg.get("symbol", "SPY")

    if sym not in price_data or "^VIX9D" not in price_data or "^VIX" not in price_data:
        return pd.Series(dtype=float), []

    df = price_data[sym]
    sig = vix_term_signal(price_data["^VIX9D"]["close"],
                         price_data["^VIX"]["close"],
                         s_cfg.get("ema_period", 5))
    sig = sig.reindex(df.index, method="ffill").fillna(False)

    cash = float(cap)
    shares = 0.0
    entry_px = entry_date = None
    trades = []
    eq = []

    for i, date in enumerate(df.index):
        o = df.loc[date, "open"]
        c = df.loc[date, "close"]
        if i > 0:
            prev_sig = bool(sig.iloc[i-1])
            if prev_sig and shares == 0:
                px = o * (1 + slip)
                alloc = cash * 0.98
                buy_c = cost.buy_cost(alloc)
                shares = (alloc - buy_c) / px
                cash -= shares * px + buy_c
                entry_px = px
                entry_date = date
            elif (not prev_sig) and shares > 0:
                px = o * (1 - slip)
                sell_c = cost.sell_cost(px * shares)
                pnl = (px - entry_px) * shares - sell_c
                cash += px * shares - sell_c
                trades.append({"entry_date": entry_date, "exit_date": date,
                               "entry_price": entry_px, "exit_price": px,
                               "shares": shares, "pnl": pnl})
                shares = 0.0
                entry_px = None
        eq.append({"date": date, "equity": cash + shares * c})

    return pd.DataFrame(eq).set_index("date")["equity"], trades
