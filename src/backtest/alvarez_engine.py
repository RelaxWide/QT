"""
Alvarez Mean Reversion 백테스트 엔진

핵심 특징:
  - 지정가 진입: 당일 저가 <= limit_price 이면 체결 (limit_price로 기록)
  - 청산 조건: RSI(2) > rsi_exit_min (과매수 반등 확인)
  - 재앙 손절: entry_price - stop_atr_mult×ATR (넓게, 드물게 발동)
  - 시간손절: max_hold_days 봉 경과 시 당일 시가
  - 포지션 사이징: 자본의 pos_size_pct% 고정 (ATR 기반 아님)

비용: config.cost 참조 (수수료 + 연말 양도소득세)
"""
import pandas as pd

from src.backtest.engine import ClosedTrade, BacktestResult
from src.backtest.costs import make_cost_model
from src.strategy.alvarez_mean_reversion import AlvarezSignal
from src.indicators.rsi import rsi as calc_rsi


def _close_trade(trades, sym, pos, exit_px, exit_date, reason):
    pnl       = (exit_px - pos["entry_price"]) * pos["shares"]
    init_risk = pos["stop_dist"] * pos["shares"]
    r_mult    = pnl / init_risk if init_risk > 0 else 0.0
    trades.append(ClosedTrade(
        symbol=sym,
        entry_date=pos["entry_date"],
        entry_price=pos["entry_price"],
        stop_initial=pos["stop"],
        exit_date=exit_date,
        exit_price=exit_px,
        exit_reason=reason,
        r_multiple=r_mult,
        pnl=pnl,
    ))


def run_alvarez_backtest(
    signals: list[AlvarezSignal],
    price_data: dict[str, pd.DataFrame],
    spy_series: pd.Series,
    cfg: dict,
) -> BacktestResult:
    al_cfg          = cfg.get("alvarez_backtest", {})
    alp             = cfg.get("alvarez_strategy", {})
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    pos_size_pct    = al_cfg.get("position_size_pct", 10.0) / 100
    max_pos         = al_cfg.get("max_positions", 10)
    slippage        = cfg["risk"]["slippage_pct"] / 100
    max_hold        = al_cfg.get("max_hold_days", 10)
    rsi_period      = alp.get("rsi_period", 2)
    rsi_exit_min    = alp.get("rsi_exit_min", 70)

    cost_model = make_cost_model(cfg)

    rsi_cache: dict[str, pd.Series] = {}
    for sym, df in price_data.items():
        rsi_cache[sym] = calc_rsi(df["close"], rsi_period)

    spy_ma200     = spy_series.rolling(200).mean().shift(1)
    regime_ok_set = set(spy_series.index[spy_series > spy_ma200])

    spy_df    = price_data.get("SPY", next(iter(price_data.values())))
    all_dates = list(spy_df.index)
    date_idx: dict[pd.Timestamp, int] = {d: i for i, d in enumerate(all_dates)}

    signals_by_date: dict[pd.Timestamp, list[AlvarezSignal]] = {}
    for sig in signals:
        signals_by_date.setdefault(sig.entry_date, []).append(sig)

    cash      = initial_capital
    positions : dict[str, dict] = {}
    trades    : list[ClosedTrade] = []
    equity_records: list[dict] = []

    for date in all_dates:
        # 손절 + 시간손절 + RSI 청산
        to_close: list[tuple] = []
        for sym, pos in positions.items():
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            bar       = df_sym.loc[date]
            bars_held = date_idx[date] - date_idx[pos["entry_date"]]

            if bar["open"] <= pos["stop"]:
                to_close.append((sym, bar["open"] * (1 - slippage), "stop_gap"))
                continue
            if bar["low"] <= pos["stop"]:
                to_close.append((sym, pos["stop"] * (1 - slippage), "stop"))
                continue
            if bars_held >= max_hold:
                to_close.append((sym, bar["open"] * (1 - slippage), "time"))
                continue
            rsi_val = rsi_cache.get(sym)
            if rsi_val is not None and date in rsi_val.index:
                rv = rsi_val.loc[date]
                if not pd.isna(rv) and rv > rsi_exit_min:
                    to_close.append((sym, bar["close"] * (1 - slippage), "target"))
                    continue

        for sym, exit_px, reason in to_close:
            pos  = positions.pop(sym)
            comm = cost_model.sell_cost(exit_px * pos["shares"])
            pnl  = (exit_px - pos["entry_price"]) * pos["shares"]
            _close_trade(trades, sym, pos, exit_px, date, reason)
            cash += exit_px * pos["shares"] - comm

        # 지정가 신규 진입
        if date in regime_ok_set and len(positions) < max_pos:
            for sig in signals_by_date.get(date, []):
                if len(positions) >= max_pos:
                    break
                if sig.symbol in positions or sig.symbol not in price_data:
                    continue
                df_sym = price_data[sig.symbol]
                if date not in df_sym.index:
                    continue

                bar = df_sym.loc[date]
                if bar["low"] > sig.limit_price:
                    continue

                entry_px  = sig.limit_price
                stop_px   = entry_px - sig.stop_distance
                if stop_px <= 0:
                    continue

                alloc    = initial_capital * pos_size_pct
                buy_comm = cost_model.buy_cost(alloc)
                if alloc + buy_comm > cash * 0.95:
                    alloc    = cash * 0.95 / (1 + cost_model.commission_pct)
                    buy_comm = cost_model.buy_cost(alloc)
                shares = alloc / entry_px
                if shares < 0.01:
                    continue

                cash -= alloc + buy_comm
                positions[sig.symbol] = {
                    "entry_date":  date,
                    "entry_price": entry_px,
                    "stop":        stop_px,
                    "stop_dist":   sig.stop_distance,
                    "shares":      shares,
                    "prev_close":  bar["close"],
                }

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
