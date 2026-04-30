"""
Monthly ETF Rotation 백테스트 엔진

매월 말 신호 기준, 다음 거래일 시가에 100% 리밸런싱.
"""
import pandas as pd
import numpy as np

from src.strategy.etf_rotation import RotationSignal
from src.backtest.costs import make_cost_model


def _next_trading_date(price_data: dict[str, pd.DataFrame], after: pd.Timestamp):
    ref = next(iter(price_data.values()))
    future = ref.index[ref.index > after]
    return future[0] if len(future) > 0 else None


def run_etf_rotation_backtest(
    signals: list[RotationSignal],
    price_data: dict[str, pd.DataFrame],
    cfg: dict,
) -> pd.Series:
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    slippage        = cfg["risk"]["slippage_pct"] / 100
    cost_model      = make_cost_model(cfg)

    # 실행일 → 신호 매핑
    exec_map: dict[pd.Timestamp, RotationSignal] = {}
    for sig in signals:
        exec_date = _next_trading_date(price_data, sig.date)
        if exec_date is not None:
            exec_map[exec_date] = sig

    all_dates_set: set[pd.Timestamp] = set()
    for df in price_data.values():
        all_dates_set.update(df.index.tolist())
    all_dates = sorted(all_dates_set)

    cash      = float(initial_capital)
    holdings  : dict[str, float] = {}   # sym → shares
    cost_basis: dict[str, float] = {}   # sym → cost per share
    equity_records: list[dict] = []

    for date in all_dates:
        # 리밸런싱
        if date in exec_map:
            sig = exec_map[date]

            # 전량 매도
            for sym, shares in list(holdings.items()):
                df_sym = price_data.get(sym)
                if df_sym is None or date not in df_sym.index:
                    continue
                sell_px = df_sym.loc[date, "open"] * (1 - slippage)
                comm    = cost_model.sell_cost(sell_px * shares)
                cb      = cost_basis.get(sym, 0.0)
                pnl     = (sell_px - cb) * shares - comm
                cash   += sell_px * shares - comm
            holdings   = {}
            cost_basis = {}

            # top 1 ETF 100% 매수
            top    = sig.top_asset
            df_top = price_data.get(top)
            if df_top is not None and date in df_top.index:
                buy_px   = df_top.loc[date, "open"] * (1 + slippage)
                alloc    = cash / (1 + cost_model.commission_pct)
                buy_comm = cost_model.buy_cost(alloc)
                shares   = alloc / buy_px
                holdings[top]   = shares
                cost_basis[top] = buy_px
                cash = max(cash - alloc - buy_comm, 0.0)

        # 자산 계산
        pos_value = sum(
            price_data[s].loc[date, "close"] * sh
            for s, sh in holdings.items()
            if s in price_data and date in price_data[s].index
        )
        equity_records.append({"date": date, "equity": cash + pos_value})

    equity_curve = pd.DataFrame(equity_records).set_index("date")["equity"]
    return equity_curve


def compute_rotation_metrics(equity: pd.Series, initial_capital: float) -> dict:
    daily_ret = equity.pct_change().dropna()
    total_ret = (equity.iloc[-1] - initial_capital) / initial_capital

    n_years = len(equity) / 252
    cagr    = (equity.iloc[-1] / initial_capital) ** (1 / n_years) - 1 if n_years > 0 else 0

    running_max = equity.cummax()
    drawdown    = (equity - running_max) / running_max
    max_dd      = drawdown.min()

    dd_dur = max_dd_dur = 0
    peak = equity.iloc[0]
    for val in equity:
        if val >= peak:
            peak   = val
            dd_dur = 0
        else:
            dd_dur    += 1
            max_dd_dur = max(max_dd_dur, dd_dur)

    sharpe  = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    down_r  = daily_ret[daily_ret < 0].std()
    sortino = daily_ret.mean() / down_r * np.sqrt(252) if down_r > 0 else 0
    calmar  = cagr / abs(max_dd) if max_dd != 0 else 0

    monthly_ret = equity.resample("ME").last().pct_change().dropna()
    monthly_wr  = (monthly_ret > 0).sum() / len(monthly_ret) if len(monthly_ret) > 0 else 0

    return {
        "total_return_pct":     round(total_ret * 100, 2),
        "cagr_pct":             round(cagr * 100, 2),
        "max_drawdown_pct":     round(max_dd * 100, 2),
        "max_drawdown_days":    max_dd_dur,
        "sharpe":               round(sharpe, 4),
        "sortino":              round(sortino, 4),
        "calmar":               round(calmar, 4),
        "monthly_win_rate":     round(monthly_wr, 4),
        "monthly_observations": len(monthly_ret),
    }
