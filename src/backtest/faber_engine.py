"""
Faber TAA 백테스트 엔진 (월간 리밸런싱)

특징:
  - 매월 말 신호 기준, 다음 거래일 시가에 리밸런싱 실행
  - 보유 자산 동일비중, 현금 대신 SHY (단기국채 ETF) 보유
  - 슬리피지 적용 (시가 ± slippage)
"""
import pandas as pd
import numpy as np

from src.backtest.costs import make_cost_model


def _next_open(df: pd.DataFrame, after: pd.Timestamp) -> tuple[pd.Timestamp | None, float | None]:
    """after 이후 첫 거래일의 (날짜, 시가) 반환."""
    future = df.index[df.index > after]
    if len(future) == 0:
        return None, None
    dt = future[0]
    return dt, df.loc[dt, "open"]


def run_faber_backtest(
    signals: list,
    price_data: dict[str, pd.DataFrame],
    cfg: dict,
) -> tuple[pd.Series, pd.DataFrame]:
    """
    Returns:
        equity_curve : pd.Series (일별 자산)
        monthly_log  : pd.DataFrame (월별 보유 자산, 비중 기록)
    """
    fb_cfg          = cfg.get("faber_backtest", {})
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    slippage        = cfg["risk"]["slippage_pct"] / 100
    cash_proxy      = cfg.get("faber_taa", {}).get("cash_proxy", "SHY")
    cost_model      = make_cost_model(cfg)

    # 전체 날짜 범위: 가장 긴 ETF 기준
    all_dates_set: set[pd.Timestamp] = set()
    for df in price_data.values():
        all_dates_set.update(df.index.tolist())
    all_dates = sorted(all_dates_set)
    date_idx  = {d: i for i, d in enumerate(all_dates)}

    cash      = float(initial_capital)
    holdings  : dict[str, float] = {}   # sym → shares
    cost_basis: dict[str, float] = {}   # sym → cost per share
    monthly_log: list[dict] = []

    # 신호 → 실행일 매핑 (월말 신호 → 다음 거래일 실행)
    signal_map: dict[pd.Timestamp, object] = {s.date: s for s in signals}
    # 실행 대기: 다음날 시가에 처리
    pending_signal: object | None = None
    pending_exec_date: pd.Timestamp | None = None

    # 월말 신호별 다음 거래일 계산
    for sig in signals:
        # 아무 ETF 기준으로 다음 거래일 찾기
        ref_df = next(iter(price_data.values()))
        exec_date, _ = _next_open(ref_df, sig.date)
        if exec_date is not None:
            signal_map[exec_date] = ("rebalance", sig)

    equity_records: list[dict] = []

    for date in all_dates:
        # ── 리밸런싱 실행 ────────────────────────────────────────────────────
        if date in signal_map and isinstance(signal_map[date], tuple):
            _, sig = signal_map[date]
            n = len(sig.assets_in)

            # 현재 보유 전량 매도 (시가 - slippage - 수수료)
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

            # 신호 자산 + 현금대리(SHY) 동일비중 매수
            buy_list = list(sig.assets_in)
            n_out    = len(sig.assets_out)
            if n_out > 0 and cash_proxy in price_data:
                buy_list.extend([cash_proxy] * n_out)

            n_total  = len(sig.assets_in) + len(sig.assets_out)
            if n_total > 0:
                alloc_each = cash / n_total / (1 + cost_model.commission_pct)
                alloc_map: dict[str, float] = {}
                for sym in buy_list:
                    alloc_map[sym] = alloc_map.get(sym, 0) + alloc_each

                for sym, alloc in alloc_map.items():
                    df_sym = price_data.get(sym)
                    if df_sym is None or date not in df_sym.index:
                        continue
                    buy_px   = df_sym.loc[date, "open"] * (1 + slippage)
                    buy_comm = cost_model.buy_cost(alloc)
                    shares   = alloc / buy_px
                    holdings[sym]   = holdings.get(sym, 0) + shares
                    cost_basis[sym] = buy_px
                    cash -= alloc + buy_comm

            monthly_log.append({
                "rebalance_date": date,
                "assets_in":      sig.assets_in,
                "assets_out":     sig.assets_out,
                "n_assets":       len(sig.assets_in),
                "cash_proxy_pct": round(n_out / n_total * 100 if n_total else 0, 1),
            })

        # 일별 자산 계산
        pos_value = 0.0
        for sym, shares in holdings.items():
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            pos_value += df_sym.loc[date, "close"] * shares

        equity_records.append({"date": date, "equity": cash + pos_value})

    equity_curve = pd.DataFrame(equity_records).set_index("date")["equity"]
    monthly_df   = pd.DataFrame(monthly_log)
    return equity_curve, monthly_df


def compute_taa_metrics(equity: pd.Series, initial_capital: float) -> dict:
    """TAA 전용 지표 (R배수 없이 equity curve 기반)."""
    daily_ret = equity.pct_change().dropna()
    total_ret = (equity.iloc[-1] - initial_capital) / initial_capital

    # 연환산 (거래일 기준)
    n_days   = len(equity)
    n_years  = n_days / 252
    cagr     = (equity.iloc[-1] / initial_capital) ** (1 / n_years) - 1 if n_years > 0 else 0

    # MDD
    running_max = equity.cummax()
    drawdown    = (equity - running_max) / running_max
    max_dd      = drawdown.min()

    # 낙폭 지속 기간
    dd_dur = max_dd_dur = 0
    in_dd  = False
    peak   = equity.iloc[0]
    for val in equity:
        if val >= peak:
            peak  = val
            in_dd = False
            dd_dur = 0
        else:
            in_dd  = True
            dd_dur += 1
            max_dd_dur = max(max_dd_dur, dd_dur)

    sharpe  = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
    down_r  = daily_ret[daily_ret < 0].std()
    sortino = (daily_ret.mean() / down_r * np.sqrt(252)) if down_r > 0 else 0
    calmar  = cagr / abs(max_dd) if max_dd != 0 else 0

    # 월별 수익률 승률
    monthly_ret = equity.resample("ME").last().pct_change().dropna()
    monthly_wr  = (monthly_ret > 0).sum() / len(monthly_ret) if len(monthly_ret) > 0 else 0

    return {
        "total_return_pct":    round(total_ret * 100, 2),
        "cagr_pct":            round(cagr * 100, 2),
        "max_drawdown_pct":    round(max_dd * 100, 2),
        "max_drawdown_days":   max_dd_dur,
        "sharpe":              round(sharpe, 4),
        "sortino":             round(sortino, 4),
        "calmar":              round(calmar, 4),
        "monthly_win_rate":    round(monthly_wr, 4),
        "monthly_observations": len(monthly_ret),
    }
