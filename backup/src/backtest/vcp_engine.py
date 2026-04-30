"""
VCP (Minervini) 백테스트 엔진

매일 스캔: 각 거래일 당일 종가 기준 VCP 검출
진입: 다음 거래일 시가에 pivot 돌파 + 거래량 확장 시 매수
청산:
  1. 진입가 -7% 손절
  2. 20일 SMA 이탈
  3. SPY < MA200 (시장 레짐)
"""
import numpy as np
import pandas as pd

from src.strategy.vcp_minervini import find_vcp_signals
from src.backtest.costs import make_cost_model


def run_vcp_backtest(
    price_data: dict[str, pd.DataFrame],
    cfg: dict,
) -> tuple[pd.Series, list[dict]]:
    vcp_cfg         = cfg.get("vcp_strategy", {})
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    slippage        = cfg["risk"]["slippage_pct"] / 100
    max_pos         = vcp_cfg.get("max_positions",        10)
    stop_pct        = vcp_cfg.get("stop_loss_pct",      0.07)
    vol_mult        = vcp_cfg.get("volume_breakout_mult", 1.4)
    vol_avg_p       = vcp_cfg.get("volume_avg_period",    50)
    trail_ma_p      = vcp_cfg.get("trail_ma_period",      20)
    spy_ma_p        = vcp_cfg.get("spy_ma200_period",    200)
    cost_model      = make_cost_model(cfg)

    spy_df    = price_data["SPY"]
    spy_close = spy_df["close"]
    spy_ma200 = spy_close.rolling(spy_ma_p).mean()
    all_dates = sorted(spy_df.index)
    date_idx  = {d: i for i, d in enumerate(all_dates)}

    # 트레일 MA 캐시
    trail_ma_cache: dict[str, pd.Series] = {}
    vol_avg_cache:  dict[str, pd.Series] = {}
    for sym, df in price_data.items():
        trail_ma_cache[sym] = df["close"].rolling(trail_ma_p).mean()
        vol_avg_cache[sym]  = df["volume"].rolling(vol_avg_p).mean()

    # 매일 시그널 스캔 (S&P500이면 무거우므로 진행도 출력)
    print("  Scanning daily VCP signals...", flush=True)
    daily_signals: dict[pd.Timestamp, list[dict]] = {}
    scan_min_idx = vcp_cfg.get("ma200_period", 200) + 60
    for i, d in enumerate(all_dates):
        if i < scan_min_idx:
            continue
        if i % 50 == 0:
            print(f"    {i}/{len(all_dates)} days scanned", flush=True)
        # 레짐 필터로 사전 컷
        spy_ma = spy_ma200.iloc[i]
        if pd.isna(spy_ma) or spy_close.iloc[i] <= spy_ma:
            continue
        sigs = find_vcp_signals(price_data, d, vcp_cfg)
        if sigs:
            daily_signals[d] = sigs

    # 펜딩 진입: 시그널 발생일 익일에 pivot 돌파 + 거래량 확인
    pending_entries: dict[str, dict] = {}  # sym → {pivot, signal_date, exec_date}

    cash       = float(initial_capital)
    holdings   : dict[str, dict] = {}  # sym → {shares, entry_px, stop, signal_date}
    trades_log : list[dict] = []
    equity_records: list[dict] = []

    for di, date in enumerate(all_dates):
        # SPY 레짐 체크
        spy_ma = spy_ma200.iloc[di]
        regime_ok = not pd.isna(spy_ma) and spy_close.iloc[di] > spy_ma

        # 1. 청산 처리
        for sym in list(holdings):
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            pos    = holdings[sym]
            close  = df_sym.loc[date, "close"]
            o      = df_sym.loc[date, "open"]
            trail  = trail_ma_cache[sym].loc[date] if date in trail_ma_cache[sym].index else np.nan
            stop_p = pos["stop"]

            exit_reason = None
            exit_px = None

            # 손절 (장중 stop hit)
            l = df_sym.loc[date, "low"]
            if l <= stop_p:
                exit_reason = "stop"
                exit_px     = min(o, stop_p) * (1 - slippage)
            # 트레일 이탈 (당일 종가 기준)
            elif not pd.isna(trail) and close < trail:
                exit_reason = "trail_ma_exit"
                exit_px     = close * (1 - slippage)
            # 시장 레짐 이탈
            elif not regime_ok:
                exit_reason = "regime_exit"
                exit_px     = close * (1 - slippage)

            if exit_reason:
                sh   = pos["shares"]
                cb   = pos["entry_px"]
                comm = cost_model.sell_cost(exit_px * sh)
                pnl  = (exit_px - cb) * sh - comm
                cash += exit_px * sh - comm
                trades_log.append({
                    "date":        date,
                    "symbol":      sym,
                    "entry_date":  pos["signal_date"],
                    "entry_price": cb,
                    "exit_price":  exit_px,
                    "shares":      sh,
                    "pnl":         pnl,
                    "reason":      exit_reason,
                })
                holdings.pop(sym)

        # 2. 펜딩 진입 처리
        for sym in list(pending_entries):
            pe = pending_entries[sym]
            if pe["exec_date"] != date:
                if date > pe["exec_date"]:
                    pending_entries.pop(sym)
                continue
            pending_entries.pop(sym)
            if not regime_ok:
                continue
            if len(holdings) >= max_pos:
                continue
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            o     = df_sym.loc[date, "open"]
            h     = df_sym.loc[date, "high"]
            v     = df_sym.loc[date, "volume"]
            pivot = pe["pivot"]
            v_avg = vol_avg_cache[sym].loc[date] if date in vol_avg_cache[sym].index else np.nan

            # pivot 돌파 + 거래량 확인 (intraday 가정)
            if h <= pivot:
                continue
            if pd.isna(v_avg) or v < v_avg * vol_mult:
                continue

            entry_px = max(o, pivot) * (1 + slippage)
            equity_now = cash + sum(
                price_data[s].loc[date, "close"] * holdings[s]["shares"]
                for s in holdings if s in price_data and date in price_data[s].index
            )
            alloc      = equity_now / max_pos
            if alloc > cash * 0.98:
                alloc = cash * 0.98
            if alloc < entry_px:
                continue
            buy_comm = cost_model.buy_cost(alloc)
            if alloc + buy_comm > cash:
                alloc = cash / (1 + cost_model.commission_pct) * 0.98
                buy_comm = cost_model.buy_cost(alloc)
            shares = alloc / entry_px

            holdings[sym] = {
                "shares":      shares,
                "entry_px":    entry_px,
                "stop":        entry_px * (1 - stop_pct),
                "signal_date": pe["signal_date"],
            }
            cash -= alloc + buy_comm

        # 3. 신규 시그널 → 다음 거래일 펜딩 등록
        if date in daily_signals and di + 1 < len(all_dates):
            next_d = all_dates[di + 1]
            for sig in daily_signals[date]:
                sym = sig["symbol"]
                if sym in holdings or sym in pending_entries:
                    continue
                pending_entries[sym] = {
                    "pivot":       sig["pivot_price"],
                    "signal_date": date,
                    "exec_date":   next_d,
                }

        # 4. 일별 자산 기록
        pos_value = sum(
            price_data[s].loc[date, "close"] * holdings[s]["shares"]
            for s in holdings if s in price_data and date in price_data[s].index
        )
        equity_records.append({"date": date, "equity": cash + pos_value})

    equity_curve = pd.DataFrame(equity_records).set_index("date")["equity"]
    return equity_curve, trades_log


def compute_vcp_metrics(equity: pd.Series, trades: list[dict], initial_capital: float) -> dict:
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
    else:
        wr = pf = 0.0

    return {
        "total_return_pct":     round(total_ret * 100, 2),
        "cagr_pct":             round(cagr * 100, 2),
        "max_drawdown_pct":     round(max_dd * 100, 2),
        "sharpe":               round(sharpe, 4),
        "sortino":              round(sortino, 4),
        "calmar":               round(calmar, 4),
        "n_trades":             len(trades),
        "win_rate":             round(wr, 4),
        "profit_factor":        round(pf, 4) if pf != float("inf") else 999.99,
    }
