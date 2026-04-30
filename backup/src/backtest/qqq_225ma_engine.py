"""
225MA QQQ 백테스트 — 신호 변환 시 다음날 시가 매매
"""
import numpy as np
import pandas as pd

from src.strategy.qqq_225ma import generate_signal
from src.backtest.costs import make_cost_model


def run_225ma_backtest(df: pd.DataFrame, cfg: dict) -> tuple[pd.Series, list[dict]]:
    s_cfg     = cfg.get("qqq_225ma_strategy", {})
    cap       = cfg["backtest"]["initial_capital_usd"]
    slippage  = cfg["risk"]["slippage_pct"] / 100
    ma_p      = s_cfg.get("ma_period", 225)
    cost      = make_cost_model(cfg)

    sig = generate_signal(df["close"], ma_p)
    cash = float(cap)
    shares = 0.0
    entry_px = None
    entry_date = None
    trades = []
    eq = []

    prev_sig = False
    for i, date in enumerate(df.index):
        o = df.loc[date, "open"]
        c = df.loc[date, "close"]

        # 시가 매매: 전일 신호 변경 → 오늘 시가 체결
        if i > 0:
            today_sig = bool(sig.iloc[i-1])
            if today_sig and shares == 0:
                px = o * (1 + slippage)
                alloc = cash * 0.98
                buy_c = cost.buy_cost(alloc)
                shares = (alloc - buy_c) / px
                cash -= shares * px + buy_c
                entry_px = px
                entry_date = date
            elif (not today_sig) and shares > 0:
                px = o * (1 - slippage)
                sell_c = cost.sell_cost(px * shares)
                pnl = (px - entry_px) * shares - sell_c
                cash += px * shares - sell_c
                trades.append({
                    "entry_date": entry_date, "exit_date": date,
                    "entry_price": entry_px, "exit_price": px,
                    "shares": shares, "pnl": pnl,
                })
                shares = 0.0
                entry_px = None

        eq.append({"date": date, "equity": cash + shares * c})

    equity = pd.DataFrame(eq).set_index("date")["equity"]
    return equity, trades


def compute_metrics(equity: pd.Series, trades: list[dict], cap: float) -> dict:
    daily = equity.pct_change().dropna()
    n_yr = len(equity) / 252
    cagr = (equity.iloc[-1] / cap) ** (1 / n_yr) - 1 if n_yr > 0 else 0
    total = (equity.iloc[-1] - cap) / cap
    rmax = equity.cummax()
    dd = (equity - rmax) / rmax
    mdd = dd.min()
    sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0
    if trades:
        wins = sum(1 for t in trades if t["pnl"] > 0)
        gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
        wr = wins / len(trades)
        pf = gp / gl if gl > 0 else float("inf")
    else:
        wr = pf = 0.0
    return {
        "total_return_pct": round(total * 100, 2),
        "cagr_pct":         round(cagr * 100, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "sharpe":           round(sharpe, 4),
        "n_trades":         len(trades),
        "win_rate":         round(wr, 4),
        "profit_factor":    round(pf, 4) if pf != float("inf") else 999.99,
    }
