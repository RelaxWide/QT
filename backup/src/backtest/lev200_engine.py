"""
Leveraged 200MA 엔진 — 신호 자산 200MA 판단, leveraged 자산 보유, 현금 회피
"""
import pandas as pd

from src.strategy.leveraged_200ma import leveraged_signal
from src.backtest.costs import make_cost_model


def run_lev200_backtest(price_data: dict[str, pd.DataFrame], cfg: dict) -> tuple[pd.Series, list[dict]]:
    s_cfg     = cfg.get("leveraged_200ma_strategy", {})
    cap       = cfg["backtest"]["initial_capital_usd"]
    slip      = cfg["risk"]["slippage_pct"] / 100
    cost      = make_cost_model(cfg)
    sig_sym   = s_cfg.get("signal_symbol", "SPY")
    lev_sym   = s_cfg.get("leveraged_symbol", "UPRO")
    ma_p      = s_cfg.get("ma_period", 200)
    buf       = s_cfg.get("buffer_pct", 0.0)

    if sig_sym not in price_data or lev_sym not in price_data:
        return pd.Series(dtype=float), []

    sig_df = price_data[sig_sym]
    lev_df = price_data[lev_sym]
    sig = leveraged_signal(sig_df["close"], ma_p, buf)

    # leveraged 자산 거래일 기준
    common = lev_df.index.intersection(sig.index)
    sig_aligned = sig.reindex(common, method="ffill").fillna(False)

    cash = float(cap)
    shares = 0.0
    entry_px = entry_date = None
    trades = []
    eq = []

    for i, date in enumerate(common):
        o = lev_df.loc[date, "open"]
        c = lev_df.loc[date, "close"]
        if i > 0:
            prev = bool(sig_aligned.iloc[i-1])
            if prev and shares == 0:
                px = o * (1 + slip)
                alloc = cash * 0.98
                buy_c = cost.buy_cost(alloc)
                shares = (alloc - buy_c) / px
                cash -= shares * px + buy_c
                entry_px = px
                entry_date = date
            elif (not prev) and shares > 0:
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
