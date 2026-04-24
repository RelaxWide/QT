"""
Weinstein Stage 2 백테스트 엔진

진입 스캔: 수요일 (W-WED 주봉 기준)
청산: 매일 close < 30주 MA → 다음 거래일 시가 (일별 모니터링)
사이징: 동일비중 (자본 / max_positions)

비용: config.cost 참조 (수수료 + 연말 양도소득세)
"""
import pandas as pd
import numpy as np

from src.backtest.engine import ClosedTrade, BacktestResult
from src.backtest.costs import make_cost_model
from src.strategy.weinstein_stage2 import WeinsteinSignal, _resample_weekly


def _close_trade(trades, sym, pos, exit_px, exit_date, reason):
    pnl    = (exit_px - pos["entry_price"]) * pos["shares"]
    cost   = pos["entry_price"] * pos["shares"]
    r_mult = pnl / cost if cost > 0 else 0.0
    trades.append(ClosedTrade(
        symbol=sym,
        entry_date=pos["entry_date"],
        entry_price=pos["entry_price"],
        stop_initial=0.0,
        exit_date=exit_date,
        exit_price=exit_px,
        exit_reason=reason,
        r_multiple=r_mult,
        pnl=pnl,
    ))


def run_weinstein_backtest(
    signals: list[WeinsteinSignal],
    price_data: dict[str, pd.DataFrame],
    spy_series: pd.Series,
    cfg: dict,
) -> BacktestResult:
    w_cfg           = cfg.get("weinstein_backtest", {})
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    max_pos         = w_cfg.get("max_positions", 15)
    slippage        = cfg["risk"]["slippage_pct"] / 100
    ma30_p          = cfg.get("weinstein_strategy", {}).get("ma30_period", 30)

    cap_cfg           = cfg.get("capital_mgmt", {})
    max_entries_day   = cap_cfg.get("max_new_entries_day", 999)
    max_daily_pct     = cap_cfg.get("max_daily_invest_pct", 100) / 100
    target_invest_pct = cap_cfg.get("target_invested_pct", 100) / 100

    cost_model = make_cost_model(cfg)

    ma30_daily: dict[str, pd.Series] = {}
    for sym, df in price_data.items():
        wdf    = _resample_weekly(df)
        weekly_ma30 = wdf["close"].rolling(ma30_p).mean()
        ma30_daily[sym] = weekly_ma30.reindex(df.index, method="ffill")

    spy_ma200 = spy_series.rolling(200).mean().shift(1)
    regime_ok_set = set(spy_series.index[spy_series > spy_ma200])

    spy_df    = price_data.get("SPY", next(iter(price_data.values())))
    all_dates = list(spy_df.index)

    signals_sorted = sorted(signals, key=lambda s: (s.entry_date, -s.volume_ratio))
    signals_by_date: dict[pd.Timestamp, list[WeinsteinSignal]] = {}
    for sig in signals_sorted:
        signals_by_date.setdefault(sig.entry_date, []).append(sig)

    ma30_exit_next: dict[str, str] = {}

    cash      = float(initial_capital)
    positions : dict[str, dict] = {}
    trades    : list[ClosedTrade] = []
    equity_records: list[dict] = []

    for date in all_dates:
        # MA30 이탈 청산
        for sym, reason in list(ma30_exit_next.items()):
            ma30_exit_next.pop(sym)
            if sym not in positions:
                continue
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            bar       = df_sym.loc[date]
            pos       = positions.pop(sym)
            exit_px   = bar["open"] * (1 - slippage)
            sell_comm = cost_model.sell_cost(exit_px * pos["shares"])
            gross_pnl = (exit_px - pos["entry_price"]) * pos["shares"]
            _close_trade(trades, sym, pos, exit_px, date, reason)
            cash += exit_px * pos["shares"] - sell_comm

        # MA30 이탈 매일 체크
        for sym, pos in list(positions.items()):
            if sym in ma30_exit_next:
                continue
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            c      = df_sym.loc[date, "close"]
            ma30_s = ma30_daily.get(sym)
            if ma30_s is None or date not in ma30_s.index:
                continue
            ma_val = ma30_s.loc[date]
            if not pd.isna(ma_val) and c < ma_val:
                ma30_exit_next[sym] = "ma30_exit"

        # 신규 진입
        if date in regime_ok_set and len(positions) < max_pos:
            pos_val_now = sum(
                price_data[s].loc[date, "close"] * p["shares"]
                for s, p in positions.items()
                if s in price_data and date in price_data[s].index
            )
            equity_now    = cash + pos_val_now
            daily_budget  = equity_now * max_daily_pct
            daily_spent   = 0.0
            entries_today = 0

            for sig in signals_by_date.get(date, []):
                if len(positions) >= max_pos or entries_today >= max_entries_day:
                    break
                if sig.symbol in positions or sig.symbol not in price_data:
                    continue
                df_sym = price_data[sig.symbol]
                if date not in df_sym.index:
                    continue

                cur_invested = pos_val_now + daily_spent
                if equity_now > 0 and cur_invested / equity_now >= target_invest_pct:
                    break

                bar      = df_sym.loc[date]
                entry_px = bar["open"] * (1 + slippage)
                alloc    = initial_capital / max_pos
                cost_val = min(alloc, cash * 0.95 / (1 + cost_model.commission_pct))
                buy_comm = cost_model.buy_cost(cost_val)

                if daily_spent + cost_val > daily_budget:
                    continue

                shares = cost_val / entry_px
                if shares < 0.01:
                    continue

                cash         -= cost_val + buy_comm
                daily_spent  += cost_val
                entries_today += 1
                positions[sig.symbol] = {
                    "entry_date":  date,
                    "entry_price": entry_px,
                    "shares":      shares,
                }

        # 자산 기록
        pos_value = sum(
            price_data[s].loc[date, "close"] * p["shares"]
            for s, p in positions.items()
            if s in price_data and date in price_data[s].index
        )
        equity_records.append({"date": date, "equity": cash + pos_value})

    equity_curve = pd.DataFrame(equity_records).set_index("date")["equity"]
    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        initial_capital=initial_capital,
    )
