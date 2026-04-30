"""
VIX MR 백테스트 엔진

청산 우선순위:
  1. 손절 (entry × (1 - stop_pct))
  2. VIX < exit_threshold → 다음날 시가
  3. max_hold_days 경과
사이징: 자본의 position_size_pct%

비용: config.cost 참조 (수수료 + 연말 양도소득세)
"""
import pandas as pd

from src.backtest.engine import ClosedTrade, BacktestResult
from src.backtest.costs import make_cost_model
from src.strategy.vix_mr import VIXSignal


def _close_trade(trades, pos, exit_px, exit_date, reason):
    pnl    = (exit_px - pos["entry_price"]) * pos["shares"]
    risk   = pos["entry_price"] * pos["stop_pct"] * pos["shares"]
    r_mult = pnl / risk if risk > 0 else 0.0
    trades.append(ClosedTrade(
        symbol="SPY",
        entry_date=pos["entry_date"],
        entry_price=pos["entry_price"],
        stop_initial=pos["stop"],
        exit_date=exit_date,
        exit_price=exit_px,
        exit_reason=reason,
        r_multiple=r_mult,
        pnl=pnl,
    ))


def run_vix_mr_backtest(
    signals: list[VIXSignal],
    spy_df: pd.DataFrame,
    vix_series: pd.Series,
    cfg: dict,
) -> BacktestResult:
    vix_cfg         = cfg.get("vix_mr_backtest", {})
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    pos_size_pct    = vix_cfg.get("position_size_pct", 50.0) / 100
    max_hold        = cfg.get("vix_mr_strategy", {}).get("max_hold_days", 20)
    exit_thr        = cfg.get("vix_mr_strategy", {}).get("vix_exit_threshold", 18)
    slippage        = cfg["risk"]["slippage_pct"] / 100

    cost_model = make_cost_model(cfg)

    all_dates   = list(spy_df.index)
    date_idx    = {d: i for i, d in enumerate(all_dates)}
    sig_by_date = {s.entry_date: s for s in signals}

    exit_next_open = False
    exit_reason    = ""
    position       = None
    cash           = float(initial_capital)
    trades: list[ClosedTrade] = []
    equity_records: list[dict] = []

    for date in all_dates:
        if date not in spy_df.index:
            continue
        bar = spy_df.loc[date]

        # VIX 청산 예약 → 시가 청산
        if exit_next_open and position is not None:
            exit_px  = bar["open"] * (1 - slippage)
            comm     = cost_model.sell_cost(exit_px * position["shares"])
            pnl      = (exit_px - position["entry_price"]) * position["shares"]
            _close_trade(trades, position, exit_px, date, exit_reason)
            cash          += exit_px * position["shares"] - comm
            position       = None
            exit_next_open = False

        if position is not None:
            if bar["open"] <= position["stop"]:
                exit_px  = bar["open"] * (1 - slippage)
                comm     = cost_model.sell_cost(exit_px * position["shares"])
                pnl      = (exit_px - position["entry_price"]) * position["shares"]
                _close_trade(trades, position, exit_px, date, "stop_gap")
                cash    += exit_px * position["shares"] - comm
                position = None
            elif bar["low"] <= position["stop"]:
                exit_px  = position["stop"] * (1 - slippage)
                comm     = cost_model.sell_cost(exit_px * position["shares"])
                pnl      = (exit_px - position["entry_price"]) * position["shares"]
                _close_trade(trades, position, exit_px, date, "stop")
                cash    += exit_px * position["shares"] - comm
                position = None

        if position is not None:
            bars_held = date_idx[date] - date_idx[position["entry_date"]]
            if bars_held >= max_hold:
                exit_next_open = True
                exit_reason    = "time"
            elif date in vix_series.index:
                vix_val = vix_series.loc[date]
                if not pd.isna(vix_val) and vix_val < exit_thr:
                    exit_next_open = True
                    exit_reason    = "vix_exit"

        # 신규 진입
        if position is None and not exit_next_open and date in sig_by_date:
            sig      = sig_by_date[date]
            entry_px = bar["open"] * (1 + slippage)
            stop_px  = entry_px * (1 - sig.stop_pct)
            alloc    = initial_capital * pos_size_pct
            buy_comm = cost_model.buy_cost(alloc)
            shares   = min(alloc, cash * 0.95 / (1 + cost_model.commission_pct)) / entry_px
            if shares >= 0.01:
                cash -= entry_px * shares + buy_comm
                position = {
                    "entry_date":  date,
                    "entry_price": entry_px,
                    "stop":        stop_px,
                    "stop_pct":    sig.stop_pct,
                    "shares":      shares,
                }

        pos_val = spy_df.loc[date, "close"] * position["shares"] if position else 0.0
        equity_records.append({"date": date, "equity": cash + pos_val})

    equity_curve = pd.DataFrame(equity_records).set_index("date")["equity"]
    return BacktestResult(trades=trades, equity_curve=equity_curve, initial_capital=initial_capital)
