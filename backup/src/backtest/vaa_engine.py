"""
VAA-G4 엔진 — 월말 1자산 holding
"""
import pandas as pd

from src.strategy.keller_vaa import vaa_score, select_vaa_asset
from src.backtest.costs import make_cost_model


def run_vaa_backtest(price_data: dict[str, pd.DataFrame], cfg: dict) -> tuple[pd.Series, list[dict]]:
    s_cfg     = cfg.get("vaa_strategy", {})
    cap       = cfg["backtest"]["initial_capital_usd"]
    slip      = cfg["risk"]["slippage_pct"] / 100
    offensive = s_cfg.get("offensive", ["SPY", "EFA", "EEM", "AGG"])
    defensive = s_cfg.get("defensive", ["SHY", "IEF", "LQD"])
    canary    = s_cfg.get("canary",    ["VWO", "BND"])
    cost      = make_cost_model(cfg)

    universe = list(set(offensive + defensive + canary))
    universe = [s for s in universe if s in price_data]

    monthly_close = {s: price_data[s]["close"].resample("ME").last() for s in universe}
    scores = {s: vaa_score(monthly_close[s]) for s in universe}

    all_dates = sorted({d for s in universe for d in price_data[s].index})

    # 월말 → 다음 영업일 리밸런싱
    targets = {}
    if universe:
        any_sym = universe[0]
        monthly_ends = sorted(set(monthly_close[any_sym].index))
        for me in monthly_ends:
            tgt = select_vaa_asset(scores, offensive, defensive, canary, me)
            if tgt:
                targets[me.to_period("M")] = tgt

    cash = float(cap)
    holding_sym = None
    shares = 0.0
    entry_px = entry_date = None
    trades = []
    eq = []
    prev_month = None

    for date in all_dates:
        period = pd.Timestamp(date).to_period("M")
        if prev_month is not None and period != prev_month:
            tgt = targets.get(prev_month)
            if tgt is not None and tgt != holding_sym:
                if holding_sym and shares > 0 and date in price_data[holding_sym].index:
                    px = price_data[holding_sym].loc[date, "open"] * (1 - slip)
                    sell_c = cost.sell_cost(px * shares)
                    pnl = (px - entry_px) * shares - sell_c
                    cash += px * shares - sell_c
                    trades.append({"symbol": holding_sym, "entry_date": entry_date,
                                   "exit_date": date, "entry_price": entry_px,
                                   "exit_price": px, "shares": shares, "pnl": pnl})
                    shares = 0.0
                    holding_sym = None
                if tgt in price_data and date in price_data[tgt].index:
                    px = price_data[tgt].loc[date, "open"] * (1 + slip)
                    alloc = cash * 0.98
                    buy_c = cost.buy_cost(alloc)
                    shares = (alloc - buy_c) / px
                    cash -= shares * px + buy_c
                    holding_sym = tgt
                    entry_px = px
                    entry_date = date
        prev_month = period

        if holding_sym and date in price_data[holding_sym].index:
            equity = cash + shares * price_data[holding_sym].loc[date, "close"]
        else:
            equity = cash + (shares * entry_px if shares > 0 else 0)
        eq.append({"date": date, "equity": equity})

    return pd.DataFrame(eq).set_index("date")["equity"], trades
