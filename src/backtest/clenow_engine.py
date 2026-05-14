"""
Clenow 주간 모멘텀 백테스트 엔진

진입 스캔: 매주 수요일 스코어 계산 → 다음 거래일(목요일) 시가 집행
청산: 매일 MA100 이탈 체크 → 다음 거래일 시가
레짐: SPY > MA200 아니면 전량 현금 보유
"""
import numpy as np
import pandas as pd

from src.strategy.clenow_momentum import compute_scores
from src.backtest.costs import make_cost_model


def run_clenow_backtest(
    price_data: dict[str, pd.DataFrame],
    cfg: dict,
) -> tuple[pd.Series, list[dict]]:
    cl_cfg          = cfg.get("clenow_strategy", {})
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    slippage        = cfg["risk"]["slippage_pct"] / 100
    max_pos         = cl_cfg.get("max_positions", 20)
    ma200_p         = cl_cfg.get("spy_ma200_period", 200)
    cost_model      = make_cost_model(cfg)

    cap_cfg           = cfg.get("capital_mgmt", {})
    max_entries_day   = cap_cfg.get("max_new_entries_day", 999)
    max_daily_pct     = cap_cfg.get("max_daily_invest_pct", 100) / 100
    target_invest_pct = cap_cfg.get("target_invested_pct", 100) / 100

    # 레짐/벤치마크 인덱스 (US: SPY / KR: ^KS11)
    regime_index = cfg.get("market", {}).get("regime_index", "SPY")
    cl_cfg.setdefault("index_ticker", regime_index)

    spy_df    = price_data[regime_index]
    spy_close = spy_df["close"]
    spy_ma200 = spy_close.rolling(ma200_p).mean()

    all_dates = sorted(spy_df.index)

    # 주간 수요일(또는 최근 거래일) 인덱스
    weekly_dates: list[pd.Timestamp] = []
    seen_weeks: set = set()
    for d in all_dates:
        wk = (d.year, d.isocalendar()[1])
        if d.weekday() == 2:  # 수요일
            weekly_dates.append(d)
            seen_weeks.add(wk)
        elif d == all_dates[-1] and wk not in seen_weeks:
            weekly_dates.append(d)

    # 수요일 → 다음 거래일(목요일) 매핑
    date_idx   = {d: i for i, d in enumerate(all_dates)}
    exec_map: dict[pd.Timestamp, pd.Timestamp] = {}
    for wed in weekly_dates:
        i = date_idx[wed]
        for offset in range(1, 6):
            if i + offset < len(all_dates):
                exec_map[wed] = all_dates[i + offset]
                break

    # MA100 캐시 (매일 청산 체크용)
    ma100_p = cl_cfg.get("ma100_period", 100)
    ma100_cache: dict[str, pd.Series] = {}
    for sym, df in price_data.items():
        ma100_cache[sym] = df["close"].rolling(ma100_p).mean()

    # 수요일별 스코어 계산
    print("  Computing weekly momentum scores...", flush=True)
    score_cache: dict[pd.Timestamp, dict[str, float]] = {}
    for idx_w, wed in enumerate(weekly_dates):
        if idx_w % 20 == 0:
            print(f"    {idx_w}/{len(weekly_dates)} weeks done", flush=True)
        score_cache[wed] = compute_scores(price_data, wed, cl_cfg)

    # 역방향 조회: exec_date → 해당 신호 수요일
    exec_to_wed: dict[pd.Timestamp, pd.Timestamp] = {v: k for k, v in exec_map.items()}

    # MA100 이탈 → 다음 거래일 청산 예약
    ma100_exit_next: dict[str, str] = {}

    # ── 백테스트 루프 ──────────────────────────────────────────────────────
    cash      = float(initial_capital)
    holdings  : dict[str, float] = {}   # sym → shares
    cost_basis: dict[str, float] = {}   # sym → cost per share (entry_px * (1+comm))
    rebal_log : list[dict] = []
    equity_records: list[dict] = []

    for date in all_dates:
        # MA100 이탈 → 다음날 청산 집행
        for sym, reason in list(ma100_exit_next.items()):
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
            pnl     = (sell_px - cb) * sh - comm
            cash   += sell_px * sh - comm

        # 리밸런싱 집행일 (수요일 스캔 → 다음날)
        if date in exec_to_wed:
            wed    = exec_to_wed[date]
            scores = score_cache.get(wed, {})

            # SPY 레짐 체크 (신호일 기준)
            wed_idx = date_idx[wed]
            spy_ma  = spy_ma200.iloc[wed_idx] if wed_idx < len(spy_ma200) else np.nan
            spy_c   = spy_close.iloc[wed_idx]
            regime_ok = not pd.isna(spy_ma) and spy_c > spy_ma

            if regime_ok and scores:
                top_syms = sorted(scores, key=lambda s: scores[s], reverse=True)[:max_pos]
            else:
                top_syms = []  # 레짐 아웃 → 전량 현금

            # 현재 보유 중 탈락 종목 매도
            to_sell = [s for s in list(holdings) if s not in top_syms]
            for sym in to_sell:
                df_sym = price_data.get(sym)
                if df_sym is None or date not in df_sym.index:
                    continue
                open_px = df_sym.loc[date, "open"]
                if pd.isna(open_px) or open_px <= 0:
                    continue   # 거래정지/데이터누락 — 다음 날로 보류
                sh      = holdings.pop(sym)
                cb      = cost_basis.pop(sym, 0.0)
                sell_px = open_px * (1 - slippage)
                comm    = cost_model.sell_cost(sell_px * sh)
                pnl     = (sell_px - cb) * sh - comm
                cash   += sell_px * sh - comm

            # 신규 진입 종목 매수 (동일비중, score 내림차순)
            new_syms = [s for s in top_syms if s not in holdings and s in price_data]
            new_syms_ranked = sorted(new_syms, key=lambda s: scores.get(s, 0), reverse=True)
            n_slots = len(top_syms)
            if n_slots > 0:
                # 전체 equity를 균등 배분 목표 (재조정 포함)
                total_equity = cash + sum(
                    price_data[s].loc[date, "open"] * sh
                    for s, sh in holdings.items()
                    if s in price_data and date in price_data[s].index
                )
                target_alloc = total_equity / n_slots

                # 기존 보유 종목 비중 재조정 (초과 보유 시 일부 매도)
                for sym in list(holdings):
                    df_sym = price_data.get(sym)
                    if df_sym is None or date not in df_sym.index:
                        continue
                    cur_val = df_sym.loc[date, "open"] * holdings[sym]
                    diff    = cur_val - target_alloc
                    if diff > target_alloc * 0.1:
                        sell_p      = df_sym.loc[date, "open"] * (1 - slippage)
                        trim_shares = (diff * 0.5) / sell_p
                        comm        = cost_model.sell_cost(sell_p * trim_shares)
                        cb          = cost_basis.get(sym, 0.0)
                        pnl         = (sell_p - cb) * trim_shares - comm
                        cash       += sell_p * trim_shares - comm
                        holdings[sym] -= trim_shares

                # 일일 예산 / 최대 진입 수 계산
                pos_val_now   = sum(
                    price_data[s].loc[date, "open"] * sh
                    for s, sh in holdings.items()
                    if s in price_data and date in price_data[s].index
                )
                equity_now    = cash + pos_val_now
                daily_budget  = equity_now * max_daily_pct
                daily_spent   = 0.0
                entries_today = 0

                # 신규 매수 (score 순위 기준)
                for sym in new_syms_ranked:
                    if entries_today >= max_entries_day:
                        break
                    cur_invested = pos_val_now + daily_spent
                    if equity_now > 0 and cur_invested / equity_now >= target_invest_pct:
                        break
                    df_sym = price_data.get(sym)
                    if df_sym is None or date not in df_sym.index:
                        continue
                    open_px = df_sym.loc[date, "open"]
                    if pd.isna(open_px) or open_px <= 0:
                        continue
                    buy_px   = open_px * (1 + slippage)
                    alloc    = min(target_alloc, cash * 0.98)
                    if daily_spent + alloc > daily_budget:
                        continue
                    if alloc < buy_px:
                        continue
                    buy_comm = cost_model.buy_cost(alloc)
                    if alloc + buy_comm > cash:
                        alloc = cash / (1 + cost_model.commission_pct) * 0.98
                        buy_comm = cost_model.buy_cost(alloc)
                    shares = alloc / buy_px
                    holdings[sym]      = holdings.get(sym, 0) + shares
                    cost_basis[sym]    = buy_px
                    cash              -= alloc + buy_comm
                    daily_spent       += alloc
                    entries_today     += 1

            rebal_log.append({
                "date":      date,
                "regime":    regime_ok,
                "n_held":    len(holdings),
                "top_score": max(scores.values()) if scores else 0,
            })

        # MA100 이탈 매일 체크 (리밸런싱과 독립적으로)
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

        # 일별 자산 계산
        pos_value = sum(
            price_data[s].loc[date, "close"] * sh
            for s, sh in holdings.items()
            if s in price_data and date in price_data[s].index
        )
        equity_records.append({"date": date, "equity": cash + pos_value})

    equity_curve = pd.DataFrame(equity_records).set_index("date")["equity"]
    return equity_curve, rebal_log


def compute_clenow_metrics(equity: pd.Series, initial_capital: float) -> dict:
    daily_ret = equity.pct_change().dropna()
    total_ret = (equity.iloc[-1] - initial_capital) / initial_capital
    n_years   = len(equity) / 252
    cagr      = (equity.iloc[-1] / initial_capital) ** (1 / n_years) - 1 if n_years > 0 else 0

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
        "total_return_pct":     round(total_ret * 100, 2),
        "cagr_pct":             round(cagr * 100, 2),
        "max_drawdown_pct":     round(max_dd * 100, 2),
        "max_drawdown_days":    max_dd_dur,
        "sharpe":               round(sharpe, 4),
        "sortino":              round(sortino, 4),
        "calmar":               round(calmar, 4),
        "monthly_win_rate":     round(monthly_wr, 4),
    }
