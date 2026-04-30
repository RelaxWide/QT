"""
Connors RSI Mean Reversion 백테스트 엔진

매일 스캔: 종가 기준 CRSI 신호 → 다음 거래일 시가 진입
청산: CRSI > 70 또는 종가 > 5일 SMA → 다음 거래일 시가
"""
import numpy as np
import pandas as pd

from src.strategy.connors_rsi_strategy import find_connors_signals
from src.indicators.connors_rsi import connors_rsi
from src.backtest.costs import make_cost_model


def run_connors_rsi_backtest(
    price_data: dict[str, pd.DataFrame],
    cfg: dict,
) -> tuple[pd.Series, list[dict]]:
    cr_cfg          = cfg.get("connors_rsi_strategy", {})
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    slippage        = cfg["risk"]["slippage_pct"] / 100
    pos_pct         = cr_cfg.get("position_size_pct", 10) / 100
    max_pos         = cr_cfg.get("max_positions",      5)
    exit_thr        = cr_cfg.get("exit_threshold",    70)
    short_ma_p      = cr_cfg.get("short_ma_period",    5)
    ma200_p         = cr_cfg.get("ma200_period",     200)
    vol_avg_p       = cr_cfg.get("vol_avg_period",    50)
    stop_pct        = cr_cfg.get("stop_pct",        0.05)
    cost_model      = make_cost_model(cfg)

    spy_df    = price_data["SPY"]
    all_dates = sorted(spy_df.index)

    # 캐시
    print("  Computing Connors RSI indicators...", flush=True)
    crsi_cache:   dict[str, pd.Series] = {}
    ma200_cache:  dict[str, pd.Series] = {}
    short_ma_cache: dict[str, pd.Series] = {}
    vol_cache:    dict[str, pd.Series] = {}
    for i, (sym, df) in enumerate(price_data.items()):
        if i % 100 == 0:
            print(f"    {i}/{len(price_data)} symbols", flush=True)
        crsi_cache[sym]     = connors_rsi(df["close"])
        ma200_cache[sym]    = df["close"].rolling(ma200_p).mean()
        short_ma_cache[sym] = df["close"].rolling(short_ma_p).mean()
        vol_cache[sym]      = df["volume"].rolling(vol_avg_p).mean()

    cash       = float(initial_capital)
    holdings   : dict[str, dict] = {}
    pending_exits: dict[str, str] = {}
    trades_log : list[dict] = []
    equity_records: list[dict] = []

    for date in all_dates:
        # 1. 펜딩 청산 (어제 신호 → 오늘 시가)
        for sym in list(pending_exits):
            reason = pending_exits.pop(sym)
            if sym not in holdings:
                continue
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            pos     = holdings[sym]
            exit_px = df_sym.loc[date, "open"] * (1 - slippage)
            sh      = pos["shares"]
            comm    = cost_model.sell_cost(exit_px * sh)
            pnl     = (exit_px - pos["entry_px"]) * sh - comm
            cash   += exit_px * sh - comm
            trades_log.append({
                "date":        date,
                "symbol":      sym,
                "entry_date":  pos["entry_date"],
                "entry_price": pos["entry_px"],
                "exit_price":  exit_px,
                "shares":      sh,
                "pnl":         pnl,
                "reason":      reason,
            })
            holdings.pop(sym)

        # 2. 손절 (장중)
        for sym in list(holdings):
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            pos     = holdings[sym]
            stop_p  = pos["stop"]
            l       = df_sym.loc[date, "low"]
            o       = df_sym.loc[date, "open"]
            if l <= stop_p:
                exit_px = min(o, stop_p) * (1 - slippage)
                sh      = pos["shares"]
                comm    = cost_model.sell_cost(exit_px * sh)
                pnl     = (exit_px - pos["entry_px"]) * sh - comm
                cash   += exit_px * sh - comm
                trades_log.append({
                    "date":        date,
                    "symbol":      sym,
                    "entry_date":  pos["entry_date"],
                    "entry_price": pos["entry_px"],
                    "exit_price":  exit_px,
                    "shares":      sh,
                    "pnl":         pnl,
                    "reason":      "stop",
                })
                holdings.pop(sym)

        # 3. 청산 신호 (CRSI > 70 또는 종가 > 5MA) → 익일 시가 청산 예약
        for sym in list(holdings):
            if sym in pending_exits:
                continue
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            c      = df_sym.loc[date, "close"]
            crsi_v = crsi_cache[sym].loc[date] if date in crsi_cache[sym].index else np.nan
            sma_v  = short_ma_cache[sym].loc[date] if date in short_ma_cache[sym].index else np.nan
            if not pd.isna(crsi_v) and crsi_v > exit_thr:
                pending_exits[sym] = "crsi_exit"
            elif not pd.isna(sma_v) and c > sma_v:
                pending_exits[sym] = "ma_exit"

        # 4. 신규 진입 (오늘 신호 → 익일 시가) — 펜딩으로 처리
        # 사실 단순화: 오늘 신호를 today's close 기준으로 잡고 다음 루프 시작에서 진입
        # 여기서는 today already has the signal, execute next day open via pending mechanism
        # 간단화를 위해: today scan signals → tomorrow open execute
        if len(holdings) < max_pos:
            sigs = find_connors_signals(
                price_data, date, cr_cfg,
                crsi_cache, ma200_cache, vol_cache,
            )
            # 다음 거래일 인덱스
            try:
                next_idx = all_dates.index(date) + 1
            except ValueError:
                next_idx = -1
            if 0 < next_idx < len(all_dates):
                next_d = all_dates[next_idx]
                slots  = max_pos - len(holdings)
                for sig in sigs[:slots]:
                    sym = sig["symbol"]
                    if sym in holdings or sym in [t.get("_pending_sym") for t in []]:
                        continue
                    df_sym = price_data.get(sym)
                    if df_sym is None or next_d not in df_sym.index:
                        continue
                    o = df_sym.loc[next_d, "open"]
                    entry_px = o * (1 + slippage)
                    equity_now = cash + sum(
                        price_data[s].loc[date, "close"] * holdings[s]["shares"]
                        for s in holdings if s in price_data and date in price_data[s].index
                    )
                    alloc = equity_now * pos_pct
                    if alloc > cash * 0.98:
                        alloc = cash * 0.98
                    if alloc < entry_px:
                        continue
                    buy_comm = cost_model.buy_cost(alloc)
                    if alloc + buy_comm > cash:
                        alloc    = cash / (1 + cost_model.commission_pct) * 0.98
                        buy_comm = cost_model.buy_cost(alloc)
                    shares = alloc / entry_px
                    holdings[sym] = {
                        "shares":     shares,
                        "entry_px":   entry_px,
                        "entry_date": next_d,
                        "stop":       entry_px * (1 - stop_pct),
                    }
                    cash -= alloc + buy_comm

        # 5. 일별 자산
        pos_value = sum(
            price_data[s].loc[date, "close"] * holdings[s]["shares"]
            for s in holdings if s in price_data and date in price_data[s].index
        )
        equity_records.append({"date": date, "equity": cash + pos_value})

    equity_curve = pd.DataFrame(equity_records).set_index("date")["equity"]
    return equity_curve, trades_log


def compute_connors_metrics(equity: pd.Series, trades: list[dict], initial_capital: float) -> dict:
    daily_ret = equity.pct_change().dropna()
    n_years   = len(equity) / 252
    cagr      = (equity.iloc[-1] / initial_capital) ** (1 / n_years) - 1 if n_years > 0 else 0
    total_ret = (equity.iloc[-1] - initial_capital) / initial_capital

    running_max = equity.cummax()
    drawdown    = (equity - running_max) / running_max
    max_dd      = drawdown.min()

    sharpe  = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    down_r  = daily_ret[daily_ret < 0].std()
    sortino = daily_ret.mean() / down_r * np.sqrt(252) if down_r > 0 else 0
    calmar  = cagr / abs(max_dd) if max_dd != 0 else 0

    if trades:
        wins         = sum(1 for t in trades if t["pnl"] > 0)
        gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        gross_loss   = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
        wr = wins / len(trades)
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        avg_hold = np.mean([
            (pd.Timestamp(t["date"]) - pd.Timestamp(t["entry_date"])).days
            for t in trades
        ])
    else:
        wr = pf = avg_hold = 0.0

    return {
        "total_return_pct": round(total_ret * 100, 2),
        "cagr_pct":         round(cagr * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe":           round(sharpe, 4),
        "sortino":          round(sortino, 4),
        "calmar":           round(calmar, 4),
        "n_trades":         len(trades),
        "win_rate":         round(wr, 4),
        "profit_factor":    round(pf, 4) if pf != float("inf") else 999.99,
        "avg_hold_days":    round(avg_hold, 1),
    }
