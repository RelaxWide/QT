"""
52주 신고가 백테스트 엔진

청산 로직 (우선순위):
  1. 갭다운 초기 손절 (open ≤ stop)
  2. 장중 초기 손절 (low ≤ stop)
  3. 25% 피크 트레일 (close < peak_close × (1 - trail_pct))
  4. MA200 이탈 → 다음날 시가 청산 (추세 붕괴)
  사이징: ATR 기반 (risk_per_trade_pct % 리스크 고정)

비용: config.cost 참조 (수수료 + 연말 양도소득세)
"""
import pandas as pd

from src.backtest.engine import ClosedTrade, BacktestResult
from src.backtest.costs import make_cost_model
from src.strategy.high52_breakout import High52Signal


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


def run_high52_backtest(
    signals: list[High52Signal],
    price_data: dict[str, pd.DataFrame],
    spy_series: pd.Series,
    cfg: dict,
) -> BacktestResult:
    h52_cfg        = cfg.get("high52_backtest", {})
    risk_cfg       = cfg["risk"]
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    risk_pct       = risk_cfg["risk_per_trade_pct"] / 100
    max_pos        = h52_cfg.get("max_positions", risk_cfg["max_positions"])
    slippage       = risk_cfg["slippage_pct"] / 100
    trail_pct      = cfg.get("high52_strategy", {}).get("trail_pct", 0.25)
    ma200_p        = cfg.get("high52_strategy", {}).get("ma200_period", 200)

    cost_model = make_cost_model(cfg)

    ma200_cache: dict[str, pd.Series] = {}
    for sym, df in price_data.items():
        ma200_cache[sym] = df["close"].rolling(ma200_p).mean()

    spy_ma200     = spy_series.rolling(200).mean().shift(1)
    regime_ok_set = set(spy_series.index[spy_series > spy_ma200])

    spy_df    = price_data.get("SPY", next(iter(price_data.values())))
    all_dates = list(spy_df.index)

    signals_by_date: dict[pd.Timestamp, list[High52Signal]] = {}
    for sig in signals:
        signals_by_date.setdefault(sig.entry_date, []).append(sig)

    ma200_exit_next: dict[str, str] = {}

    cash      = initial_capital
    positions : dict[str, dict] = {}
    trades    : list[ClosedTrade] = []
    equity_records: list[dict] = []

    for date in all_dates:
        # MA200 이탈 다음날 시가 청산
        for sym, reason in list(ma200_exit_next.items()):
            ma200_exit_next.pop(sym)
            if sym not in positions:
                continue
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            pos     = positions.pop(sym)
            exit_px = df_sym.loc[date, "open"] * (1 - slippage)
            comm    = cost_model.sell_cost(exit_px * pos["shares"])
            pnl     = (exit_px - pos["entry_price"]) * pos["shares"]
            _close_trade(trades, sym, pos, exit_px, date, reason)
            cash += exit_px * pos["shares"] - comm

        # 손절 + 25% 트레일 체크
        to_close: list[tuple] = []
        for sym, pos in positions.items():
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            bar = df_sym.loc[date]
            c   = bar["close"]

            pos["peak_close"] = max(pos["peak_close"], c)
            trail_stop = pos["peak_close"] * (1 - trail_pct)

            if bar["open"] <= pos["stop"]:
                to_close.append((sym, bar["open"] * (1 - slippage), "stop_gap"))
                continue
            if bar["low"] <= pos["stop"]:
                to_close.append((sym, pos["stop"] * (1 - slippage), "stop"))
                continue
            if c < trail_stop:
                to_close.append((sym, c * (1 - slippage), "trail"))
                continue

            ma200_val = ma200_cache.get(sym)
            if ma200_val is not None and date in ma200_val.index:
                mv = ma200_val.loc[date]
                if not pd.isna(mv) and c < mv:
                    ma200_exit_next[sym] = "ma200_exit"

        for sym, exit_px, reason in to_close:
            pos  = positions.pop(sym)
            comm = cost_model.sell_cost(exit_px * pos["shares"])
            pnl  = (exit_px - pos["entry_price"]) * pos["shares"]
            _close_trade(trades, sym, pos, exit_px, date, reason)
            cash += exit_px * pos["shares"] - comm
            ma200_exit_next.pop(sym, None)

        # 신규 진입
        if date in regime_ok_set and len(positions) < max_pos:
            for sig in signals_by_date.get(date, []):
                if len(positions) >= max_pos:
                    break
                if sig.symbol in positions or sig.symbol not in price_data:
                    continue
                df_sym = price_data[sig.symbol]
                if date not in df_sym.index:
                    continue

                bar      = df_sym.loc[date]
                entry_px = bar["open"] * (1 + slippage)
                stop_px  = entry_px - sig.stop_distance

                if stop_px <= 0 or sig.stop_distance <= 0:
                    continue

                risk_amt  = initial_capital * risk_pct
                shares    = risk_amt / sig.stop_distance
                trade_val = entry_px * shares
                buy_comm  = cost_model.buy_cost(trade_val)
                if trade_val + buy_comm > cash * 0.95:
                    shares    = (cash * 0.95) / (entry_px * (1 + cost_model.commission_pct))
                    trade_val = entry_px * shares
                    buy_comm  = cost_model.buy_cost(trade_val)
                if shares < 0.01:
                    continue

                cash -= trade_val + buy_comm
                positions[sig.symbol] = {
                    "entry_date":  date,
                    "entry_price": entry_px,
                    "stop":        stop_px,
                    "stop_dist":   sig.stop_distance,
                    "shares":      shares,
                    "peak_close":  bar["close"],
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
