"""
Connors Double Seven 백테스트 엔진 (ETF only)

청산 우선순위:
  1. 갭다운 손절 (open ≤ stop)
  2. 장중 손절 (low ≤ stop)
  3. 7일 최고 종가 도달 → 다음날 시가 청산
사이징: 동일비중 (자본 / max_positions)

비용: config.cost 참조 (수수료 + 연말 양도소득세)
"""
import pandas as pd

from src.backtest.engine import ClosedTrade, BacktestResult
from src.backtest.costs import make_cost_model
from src.strategy.double7 import Double7Signal


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


def run_double7_backtest(
    signals: list[Double7Signal],
    price_data: dict[str, pd.DataFrame],
    cfg: dict,
) -> BacktestResult:
    d7_cfg          = cfg.get("double7_backtest", {})
    initial_capital = cfg["backtest"]["initial_capital_usd"]
    max_pos         = d7_cfg.get("max_positions", 3)
    pos_size_pct    = d7_cfg.get("position_size_pct", 33.0) / 100
    slippage        = cfg["risk"]["slippage_pct"] / 100
    high7_p         = cfg.get("double7_strategy", {}).get("high7_period", 7)

    cost_model = make_cost_model(cfg)

    ref_sym   = "SPY" if "SPY" in price_data else next(iter(price_data))
    all_dates = list(price_data[ref_sym].index)

    high7_cache: dict[str, pd.Series] = {}
    for sym, df in price_data.items():
        high7_cache[sym] = df["close"].rolling(high7_p).max()

    signals_by_date: dict[pd.Timestamp, list[Double7Signal]] = {}
    for sig in signals:
        signals_by_date.setdefault(sig.entry_date, []).append(sig)

    exit_at_next_open: dict[str, str] = {}

    cash      = initial_capital
    positions : dict[str, dict] = {}
    trades    : list[ClosedTrade] = []
    equity_records: list[dict] = []

    for date in all_dates:
        # 전날 7일 최고 달성 → 오늘 시가 청산
        for sym, reason in list(exit_at_next_open.items()):
            exit_at_next_open.pop(sym)
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

        # 손절 체크
        to_close: list[tuple] = []
        for sym, pos in positions.items():
            df_sym = price_data.get(sym)
            if df_sym is None or date not in df_sym.index:
                continue
            bar = df_sym.loc[date]
            c   = bar["close"]

            if bar["open"] <= pos["stop"]:
                to_close.append((sym, bar["open"] * (1 - slippage), "stop_gap"))
                continue
            if bar["low"] <= pos["stop"]:
                to_close.append((sym, pos["stop"] * (1 - slippage), "stop"))
                continue

            h7 = high7_cache.get(sym)
            if h7 is not None and date in h7.index:
                hv = h7.loc[date]
                if not pd.isna(hv) and c >= hv:
                    exit_at_next_open[sym] = "target_7high"

        for sym, exit_px, reason in to_close:
            pos  = positions.pop(sym)
            comm = cost_model.sell_cost(exit_px * pos["shares"])
            pnl  = (exit_px - pos["entry_price"]) * pos["shares"]
            _close_trade(trades, sym, pos, exit_px, date, reason)
            cash += exit_px * pos["shares"] - comm
            exit_at_next_open.pop(sym, None)

        # 신규 진입
        if len(positions) < max_pos:
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
