"""
Residual Momentum 백테스트 엔진

리밸런싱: 매주 수요일 스코어 계산 → 다음 거래일(목요일) 시가 집행
청산: 매일 MA100 이탈 체크 → 다음 거래일 시가
레짐: SPY > MA200 아니면 전량 현금
"""
import numpy as np
import pandas as pd

from src.strategy.residual_momentum import compute_residual_scores
from src.backtest.costs import make_cost_model


def run_residual_backtest(
    price_data: dict[str, pd.DataFrame],
    cfg: dict,
) -> tuple[pd.Series, list[dict]]:
    rm_cfg          = cfg.get("residual_strategy", {})
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    slippage        = cfg["risk"]["slippage_pct"] / 100
    max_pos         = rm_cfg.get("max_positions", 20)
    ma200_p         = rm_cfg.get("spy_ma200_period", 200)
    ma100_p         = rm_cfg.get("ma100_period", 100)
    cost_model      = make_cost_model(cfg)

    spy_df    = price_data["SPY"]
    spy_close = spy_df["close"]
    spy_ma200 = spy_close.rolling(ma200_p).mean()

    all_dates = sorted(spy_df.index)
    date_idx  = {d: i for i, d in enumerate(all_dates)}

    # 주간 수요일(또는 당주 마지막 거래일) 수집
    weekly_dates: list[pd.Timestamp] = []
    seen_weeks: set = set()
    for d in all_dates:
        wk = (d.year, d.isocalendar()[1])
        if d.weekday() == 2:
            weekly_dates.append(d)
            seen_weeks.add(wk)
        elif d == all_dates[-1] and wk not in seen_weeks:
            weekly_dates.append(d)

    # 수요일 → 다음 거래일 매핑
    exec_map: dict[pd.Timestamp, pd.Timestamp] = {}
    for wed in weekly_dates:
        i = date_idx[wed]
        for offset in range(1, 6):
            if i + offset < len(all_dates):
                exec_map[wed] = all_dates[i + offset]
                break
    exec_to_wed = {v: k for k, v in exec_map.items()}

    # MA100 캐시
    ma100_cache: dict[str, pd.Series] = {}
    for sym, df in price_data.items():
        ma100_cache[sym] = df["close"].rolling(ma100_p).mean()

    # 수요일별 잔차 스코어 캐시
    print("  Computing weekly residual momentum scores...", flush=True)
    score_cache: dict[pd.Timestamp, dict[str, float]] = {}
    for idx_w, wed in enumerate(weekly_dates):
        if idx_w % 20 == 0:
            print(f"    {idx_w}/{len(weekly_dates)} weeks done", flush=True)
        spy_sub = spy_close[spy_close.index <= wed]
        score_cache[wed] = compute_residual_scores(price_data, wed, rm_cfg, spy_sub)

    # MA100 이탈 → 다음 거래일 청산 예약
    ma100_exit_next: dict[str, str] = {}

    # ── 백테스트 루프 ────────────────────────────────────────────────────
    cash       = float(initial_capital)
    holdings   : dict[str, float] = {}
    cost_basis : dict[str, float] = {}
    rebal_log  : list[dict] = []
    equity_records: list[dict] = []

    for date in all_dates:
        # MA100 이탈 청산 집행
        for sym in list(ma100_exit_next):
            ma100_exit_next.pop(sym)
            if sym not in holdings:
                continue
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            sh      = holdings.pop(sym)
            cb      = cost_basis.pop(sym, 0.0)
            sell_px = df_sym.loc[date, "open"] * (1 - slippage)
            comm    = cost_model.sell_cost(sell_px * sh)
            cash   += sell_px * sh - comm

        # 리밸런싱 집행일
        if date in exec_to_wed:
            wed    = exec_to_wed[date]
            scores = score_cache.get(wed, {})

            wed_idx   = date_idx[wed]
            spy_ma    = spy_ma200.iloc[wed_idx] if wed_idx < len(spy_ma200) else np.nan
            spy_c     = spy_close.iloc[wed_idx]
            regime_ok = not pd.isna(spy_ma) and spy_c > spy_ma

            if regime_ok and scores:
                top_syms = sorted(scores, key=lambda s: scores[s], reverse=True)[:max_pos]
            else:
                top_syms = []

            # 탈락 종목 매도
            for sym in [s for s in list(holdings) if s not in top_syms]:
                df_sym = price_data.get(sym)
                if df_sym is None or date not in df_sym.index:
                    continue
                sh      = holdings.pop(sym)
                cb      = cost_basis.pop(sym, 0.0)
                sell_px = df_sym.loc[date, "open"] * (1 - slippage)
                comm    = cost_model.sell_cost(sell_px * sh)
                cash   += sell_px * sh - comm

            # 신규 진입
            new_syms = sorted(
                [s for s in top_syms if s not in holdings and s in price_data],
                key=lambda s: scores.get(s, 0), reverse=True,
            )
            if top_syms:
                total_equity = cash + sum(
                    price_data[s].loc[date, "open"] * sh
                    for s, sh in holdings.items()
                    if s in price_data and date in price_data[s].index
                )
                target_alloc = total_equity / len(top_syms)

                for sym in new_syms:
                    df_sym = price_data.get(sym)
                    if df_sym is None or date not in df_sym.index:
                        continue
                    buy_px   = df_sym.loc[date, "open"] * (1 + slippage)
                    alloc    = min(target_alloc, cash * 0.98)
                    if alloc < buy_px:
                        continue
                    buy_comm = cost_model.buy_cost(alloc)
                    if alloc + buy_comm > cash:
                        alloc    = cash / (1 + cost_model.commission_pct) * 0.98
                        buy_comm = cost_model.buy_cost(alloc)
                    shares          = alloc / buy_px
                    holdings[sym]   = holdings.get(sym, 0) + shares
                    cost_basis[sym] = buy_px
                    cash           -= alloc + buy_comm

            rebal_log.append({
                "date":      date,
                "regime":    regime_ok,
                "n_held":    len(holdings),
                "top_score": max(scores.values()) if scores else 0,
            })

        # MA100 이탈 매일 체크
        for sym in list(holdings):
            if sym in ma100_exit_next:
                continue
            ma100_s = ma100_cache.get(sym)
            if ma100_s is None or date not in ma100_s.index:
                continue
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            c      = df_sym.loc[date, "close"]
            ma_val = ma100_s.loc[date]
            if not pd.isna(ma_val) and c < ma_val:
                ma100_exit_next[sym] = "ma100_exit"

        # 일별 자산
        pos_value = sum(
            price_data[s].loc[date, "close"] * sh
            for s, sh in holdings.items()
            if s in price_data and date in price_data[s].index
        )
        equity_records.append({"date": date, "equity": cash + pos_value})

    equity_curve = pd.DataFrame(equity_records).set_index("date")["equity"]
    return equity_curve, rebal_log


def compute_residual_metrics(equity: pd.Series, initial_capital: float) -> dict:
    daily_ret = equity.pct_change().dropna()
    n_years   = len(equity) / 252
    cagr      = (equity.iloc[-1] / initial_capital) ** (1 / n_years) - 1 if n_years > 0 else 0
    total_ret = (equity.iloc[-1] - initial_capital) / initial_capital

    running_max = equity.cummax()
    drawdown    = (equity - running_max) / running_max
    max_dd      = drawdown.min()

    dd_dur = max_dd_dur = 0
    peak   = equity.iloc[0]
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
        "total_return_pct":  round(total_ret * 100, 2),
        "cagr_pct":          round(cagr * 100, 2),
        "max_drawdown_pct":  round(max_dd * 100, 2),
        "max_drawdown_days": max_dd_dur,
        "sharpe":            round(sharpe, 4),
        "sortino":           round(sortino, 4),
        "calmar":            round(calmar, 4),
        "monthly_win_rate":  round(monthly_wr, 4),
    }
