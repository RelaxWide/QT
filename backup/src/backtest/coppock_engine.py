"""
Coppock 엔진 — 월말 신호 → 다음 거래일 시가 매매
"""
import pandas as pd

from src.strategy.coppock import generate_monthly_signals
from src.backtest.costs import make_cost_model


def run_coppock_backtest(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, list[dict]]:
    s_cfg = cfg.get("coppock_strategy", {})
    cap   = cfg["backtest"]["initial_capital_usd"]
    slip  = cfg["risk"]["slippage_pct"] / 100
    cost  = make_cost_model(cfg)

    flip = generate_monthly_signals(df["close"],
        s_cfg.get("roc1_months", 14),
        s_cfg.get("roc2_months", 11),
        s_cfg.get("wma_period", 10))

    # 월말 신호 → 다음 거래일 시가 매매
    cash = float(cap)
    shares = 0.0
    entry_px = entry_date = None
    trades = []
    eq = []
    pending_action = None  # "buy" or "sell"

    flip_dates = {d.normalize(): int(v) for d, v in flip.items() if v != 0}

    for date in df.index:
        o = df.loc[date, "open"]
        c = df.loc[date, "close"]

        # 월말 flip 후 다음 거래일 시가에 체결
        if pending_action == "buy" and shares == 0:
            px = o * (1 + slip)
            alloc = cash * 0.98
            buy_c = cost.buy_cost(alloc)
            shares = (alloc - buy_c) / px
            cash -= shares * px + buy_c
            entry_px = px
            entry_date = date
            pending_action = None
        elif pending_action == "sell" and shares > 0:
            px = o * (1 - slip)
            sell_c = cost.sell_cost(px * shares)
            pnl = (px - entry_px) * shares - sell_c
            cash += px * shares - sell_c
            trades.append({"entry_date": entry_date, "exit_date": date,
                           "entry_price": entry_px, "exit_price": px,
                           "shares": shares, "pnl": pnl})
            shares = 0.0
            entry_px = None
            pending_action = None

        # 월말이면 flip 체크 (다음 영업일에 처리)
        is_month_end = (date == df.index[df.index.to_period("M") == date.to_period("M")][-1])
        if is_month_end:
            f = flip_dates.get(date.to_period("M").to_timestamp("M").normalize())
            # resample("ME").last() 기준 월말 timestamp 매칭
            for fd, fv in flip_dates.items():
                if fd.year == date.year and fd.month == date.month:
                    f = fv
                    break
            if f == 1:
                pending_action = "buy"
            elif f == -1:
                pending_action = "sell"

        eq.append({"date": date, "equity": cash + shares * c})

    return pd.DataFrame(eq).set_index("date")["equity"], trades
