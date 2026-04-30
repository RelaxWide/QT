"""
Connors TPS 백테스트 엔진

각 ETF별 독립 운용:
  - 자본 분배: 1/N (N=ETF수)
  - 4단계 스케일인 (10/20/30/40%)
  - RSI(2)>70 → 전 레이어 청산
"""
import numpy as np
import pandas as pd

from src.strategy.connors_tps import calc_rsi, SCALE_PCT
from src.backtest.costs import make_cost_model


def run_tps_backtest(
    price_data: dict[str, pd.DataFrame],
    cfg: dict,
) -> tuple[pd.Series, list[dict]]:
    tps_cfg         = cfg.get("tps_strategy", {})
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    slippage        = cfg["risk"]["slippage_pct"] / 100
    rsi_p           = tps_cfg.get("rsi_period",       2)
    entry_thr       = tps_cfg.get("rsi_entry_max",   25)
    exit_thr        = tps_cfg.get("rsi_exit_min",    70)
    ma_p            = tps_cfg.get("ma_period",      200)
    cost_model      = make_cost_model(cfg)

    universe = [s for s in price_data if s != "_skip_"]
    n_etf    = len(universe)
    if n_etf == 0:
        return pd.Series(dtype=float), []
    per_etf_cap = initial_capital / n_etf

    # 모든 일자 합집합 (ETF별 거래일이 다를 수 있음)
    all_dates = sorted({d for sym in universe for d in price_data[sym].index})

    # 인디케이터 캐시
    rsi_cache: dict[str, pd.Series] = {}
    ma_cache:  dict[str, pd.Series] = {}
    for sym in universe:
        df = price_data[sym]
        rsi_cache[sym] = calc_rsi(df["close"], rsi_p)
        ma_cache[sym]  = df["close"].rolling(ma_p).mean()

    # 종목별 상태
    state: dict[str, dict] = {
        sym: {
            "cash":       per_etf_cap,
            "layers":     [],          # [{shares, entry_px, layer_idx}]
            "last_entry_px": None,
            "pending_entry_layer": None,  # 다음날 시가 진입할 레이어 인덱스
            "pending_exit": False,
            "consecutive_rsi_low": 0,
        } for sym in universe
    }
    trades_log : list[dict] = []
    equity_records: list[dict] = []

    for date in all_dates:
        for sym in universe:
            df = price_data[sym]
            if date not in df.index:
                continue
            s = state[sym]
            o = df.loc[date, "open"]
            c = df.loc[date, "close"]

            # 1. 펜딩 청산 (전일 RSI>70 → 오늘 시가)
            if s["pending_exit"] and s["layers"]:
                exit_px = o * (1 - slippage)
                total_sh   = sum(L["shares"] for L in s["layers"])
                total_cost = sum(L["shares"] * L["entry_px"] for L in s["layers"])
                comm = cost_model.sell_cost(exit_px * total_sh)
                pnl  = (exit_px - total_cost / total_sh) * total_sh - comm
                s["cash"] += exit_px * total_sh - comm
                trades_log.append({
                    "date":         date,
                    "symbol":       sym,
                    "exit_price":   exit_px,
                    "shares":       total_sh,
                    "n_layers":     len(s["layers"]),
                    "pnl":          pnl,
                    "reason":       "rsi_exit",
                })
                s["layers"] = []
                s["last_entry_px"] = None
            s["pending_exit"] = False

            # 2. 펜딩 진입 (전일 신호 → 오늘 시가)
            if s["pending_entry_layer"] is not None:
                layer_idx = s["pending_entry_layer"]
                s["pending_entry_layer"] = None
                if layer_idx < len(SCALE_PCT) and s["cash"] > 0:
                    entry_px = o * (1 + slippage)
                    alloc    = per_etf_cap * SCALE_PCT[layer_idx]
                    if alloc > s["cash"] * 0.98:
                        alloc = s["cash"] * 0.98
                    if alloc >= entry_px:
                        buy_comm = cost_model.buy_cost(alloc)
                        if alloc + buy_comm > s["cash"]:
                            alloc    = s["cash"] / (1 + cost_model.commission_pct) * 0.98
                            buy_comm = cost_model.buy_cost(alloc)
                        shares = alloc / entry_px
                        s["layers"].append({
                            "shares":    shares,
                            "entry_px":  entry_px,
                            "layer_idx": layer_idx,
                            "entry_date": date,
                        })
                        s["cash"] -= alloc + buy_comm
                        s["last_entry_px"] = entry_px

            # 3. 신호 평가 (오늘 종가 기준)
            rsi_v = rsi_cache[sym].loc[date] if date in rsi_cache[sym].index else np.nan
            ma_v  = ma_cache[sym].loc[date]  if date in ma_cache[sym].index  else np.nan

            # 청산 신호
            if s["layers"] and not pd.isna(rsi_v) and rsi_v > exit_thr:
                s["pending_exit"] = True
                continue

            # 진입 신호
            trend_ok = not pd.isna(ma_v) and c > ma_v
            rsi_low  = not pd.isna(rsi_v) and rsi_v < entry_thr

            # 1차 진입: RSI<25 2일 연속
            if rsi_low:
                s["consecutive_rsi_low"] += 1
            else:
                s["consecutive_rsi_low"] = 0

            if not s["layers"]:
                # 신규 진입 (1차)
                if trend_ok and s["consecutive_rsi_low"] >= 2:
                    s["pending_entry_layer"] = 0
            else:
                # 추가 스케일인 (2~4차): 종가가 직전 진입가보다 낮으면
                cur_layer = len(s["layers"])
                if cur_layer < len(SCALE_PCT):
                    if s["last_entry_px"] is not None and c < s["last_entry_px"]:
                        s["pending_entry_layer"] = cur_layer

        # 일별 자산
        total_equity = 0.0
        for sym in universe:
            s   = state[sym]
            df  = price_data[sym]
            if date in df.index:
                px = df.loc[date, "close"]
                total_equity += s["cash"] + sum(L["shares"] * px for L in s["layers"])
            else:
                # 거래일 아닌 경우: 직전 종가 사용
                prev = df.loc[df.index <= date]
                if len(prev) > 0:
                    px = prev.iloc[-1]["close"]
                    total_equity += s["cash"] + sum(L["shares"] * px for L in s["layers"])
                else:
                    total_equity += s["cash"]
        equity_records.append({"date": date, "equity": total_equity})

    equity_curve = pd.DataFrame(equity_records).set_index("date")["equity"]
    return equity_curve, trades_log


def compute_tps_metrics(equity: pd.Series, trades: list[dict], initial_capital: float) -> dict:
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
        avg_layers = np.mean([t["n_layers"] for t in trades])
    else:
        wr = pf = avg_layers = 0.0

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
        "avg_layers":       round(avg_layers, 2),
    }
